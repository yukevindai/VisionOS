"""Large, high-contrast spatial readout beside the live video (Tk widgets, so the video is never covered).

Displays signed axis movement only from the calibrated, fresh distance adapter. Proprio-style estimated axis
differences are visible as amber diagnostic values, never movement commands. Stale data clears every axis field.
"""
from __future__ import annotations

import time
import tkinter as tk
import math

BG, FG, DIM = '#ffffff', '#111827', '#6b7280'
GOOD, WARN, BAD, CUE = '#15803d', '#b45309', '#b91c1c', '#a16207'  # darker shades stay readable on white
STATE_COLORS = {'LOCKED': GOOD, 'UNCERTAIN': WARN, 'HIDDEN': WARN, 'REACQUIRING': WARN}


def estimate_preview(offset, readout, fresh, locked):
    """Old prototype's target-minus-hand numbers, for observation only, never movement commands.

    Keep this display-only branch outside DirectionalDistance.axes and speech authorization.
    The readout must already have accepted exact-frame pairing before exposing raw deltas.
    """
    if (not fresh or not locked or offset is None or readout is None or readout.delta_cm is None
            or offset.status not in ('ESTIMATED', 'LIDAR') or offset.fingertip_m is None or offset.target_m is None):
        return None
    disagreement = offset.depth_disagreement_m
    if disagreement is not None and (not math.isfinite(disagreement) or not 0 <= disagreement <= .03):
        return None
    if len(offset.target_m) != 3 or len(offset.fingertip_m) != 3 or len(readout.delta_cm) != 3:
        return None
    # Same signed metric subtraction as testing_ver_calhack/upstream/interfaces.hand_error.
    delta = tuple((goal - finger) * 100 for goal, finger in zip(offset.target_m, offset.fingertip_m))
    if not all(math.isfinite(v) for v in delta) or not all(
            math.isclose(a, b, abs_tol=1e-6) for a, b in zip(delta, readout.delta_cm)):
        return None
    qualifier = 'ESTIMATED' if offset.fingertip_estimated or offset.status == 'ESTIMATED' else 'UNVALIDATED'
    labels = {}
    for axis, value, positive, negative in zip('xyz', delta, ('RIGHT', 'DOWN', 'FORWARD'), ('LEFT', 'UP', 'BACK')):
        direction = positive if value > 0 else negative if value < 0 else f'{axis.upper()} OFFSET'
        labels[axis] = f'{direction} ≈{abs(value):.1f} cm'
    labels['qualifier'] = qualifier
    return labels


class SpatialPanel:
    def __init__(self, parent, width=520):
        self.frame = tk.Frame(parent, bg=BG, width=width, padx=14, pady=10, highlightthickness=1,
                              highlightbackground='#d1d5db')
        self.frame.grid_propagate(False)
        self.depth_photo = None

        self.frame.columnconfigure(0, minsize=100)
        for column in (1, 2, 3):
            self.frame.columnconfigure(column, minsize=(width - 130) // 3, uniform='xyz')

        def label(text='', size=18, color=FG, bold=False, font='Segoe UI', **grid):
            span = grid.pop('columnspan', 4)
            widget = tk.Label(self.frame, text=text, bg=BG, fg=color, anchor='w', justify='left',
                              font=(font, -size, 'bold' if bold else 'normal'),
                              wraplength=width - 30 if span > 1 else 0)
            widget.grid(sticky='w', columnspan=span, **grid)
            return widget
        self.target = label('TARGET: -', 24, FG, True, row=0)
        self.tracking = label('TRACKING: -', 22, DIM, True, row=1)
        self.direction = label('CAMERA RELATIVE / UNRELIABLE', 18, WARN, True, row=2)
        self.distance = label('FORWARD: UNRELIABLE', 46, BAD, True, row=3)
        self.side = label('LEFT / RIGHT: -', 40, FG, True, row=4)
        self.height = label('UP / DOWN: -', 40, FG, True, row=5)
        self.diagnostic_toggle = tk.BooleanVar(value=False)
        self.diagnostics = tk.Frame(self.frame, bg=BG)
        self.diagnostics.grid(row=8, column=0, columnspan=4, sticky='w')
        self.diagnostics.grid_remove()
        self.diagnostic_check = tk.Checkbutton(
            self.frame, text='Diagnostics: raw XYZ and total distance', variable=self.diagnostic_toggle,
            bg=BG, fg=FG, selectcolor=BG, activebackground=BG, activeforeground=FG,
            command=lambda: self.diagnostics.grid() if self.diagnostic_toggle.get() else self.diagnostics.grid_remove())
        self.diagnostic_check.grid(row=6, column=0, columnspan=4, sticky='w')
        self.total = tk.Label(self.diagnostics, text='', bg=BG, fg=DIM, font=('Segoe UI', -14))
        self.total.grid(row=0, column=0, columnspan=4, sticky='w')
        self.cells = {}
        for column, axis in enumerate(('X', 'Y', 'Z'), 1):
            tk.Label(self.diagnostics, text=axis, bg=BG, fg=DIM).grid(row=1, column=column)
        for row, name in enumerate(('FINGER', 'TARGET', 'DIFF'), 2):
            tk.Label(self.diagnostics, text=name, bg=BG, fg=DIM).grid(row=row, column=0, sticky='w')
            for column in range(1, 4):
                cell = tk.Label(self.diagnostics, text='-', bg=BG, fg=FG, font=('Consolas', -22, 'bold'))
                cell.grid(row=row, column=column, padx=12, sticky='w')
                self.cells[name, column] = cell
        self.axes_note = label('CAMERA axes: right / down / away from phone. Not body-relative. '
                               'Calibrate before movement.', 15, WARN, row=7)
        self.source = label('DEPTH SOURCE: -', 16, DIM, row=14, pady=(8, 0))
        self.confidence = label('DEPTH CONFIDENCE: -', 20, FG, True, row=9)
        self.pixels = label('2D OFFSET: -', 18, DIM, row=10)
        self.timing = label('FRAME AGE: -', 20, FG, True, row=11, pady=(6, 0))
        self.audio = label('AUDIO: OFF', 16, DIM, row=12, pady=(6, 0))
        self.depth = tk.Label(self.frame, bg=BG)
        self.depth.grid(row=15, column=0, columnspan=4, sticky='w', pady=(8, 0))
        self.details = True

    def set_details(self, show):
        """Clean view (show False): target, tracking, the three direction fields and the last spoken cue only."""
        self.details = bool(show)
        for widget in (self.direction, self.diagnostic_check, self.axes_note, self.confidence, self.pixels,
                       self.timing, self.source):
            (widget.grid if self.details else widget.grid_remove)()
        if not self.details:
            self.diagnostics.grid_remove()
        elif self.diagnostic_toggle.get():
            self.diagnostics.grid()

    def _values(self, name, values, fmt='{:+.1f}'):
        for column in range(1, 4):
            text = '-' if values is None else fmt.format(values[column - 1] * 100)
            self.cells[name, column].configure(text=text)

    def update(self, data):
        if not data:
            return
        age = time.monotonic() - data['received']
        fresh = 0 <= age <= .3
        state = data.get('tracking') or '-'
        self.target.configure(text=f"TARGET: {(data.get('target') or '-').upper()[:28]}")
        self.tracking.configure(text=f'TRACKING: {state}', fg=STATE_COLORS.get(state, BAD))
        self.timing.configure(text=f"FRAME AGE: {age * 1000:.0f} ms  |  {data.get('fps', 0):.1f} FPS",
                              fg=FG if fresh else BAD)
        audio = data.get('audio') or {}
        if not self.details:
            self.audio.configure(text=f"LAST SPOKEN: {audio.get('last_cue') or '-'}" if audio.get('enabled') else '')
        else:
            self.audio.configure(text=(f"AUDIO: {'ON' if audio.get('enabled') else 'OFF'}"
                                   + (f"  |  LAST CUE: {audio.get('last_cue') or '-'}  |  {audio.get('playback') or '-'}"
                                      f"\nSUPPRESSED: {audio.get('suppressed') or '-'}" if audio.get('enabled') else '')))
        offset = data.get('offset') if fresh else None
        distances = data.get('distances') if fresh and state == 'LOCKED' else None
        if (distances is not None and distances.axes and (distances.valid_until_monotonic is None
                                                         or time.monotonic() > distances.valid_until_monotonic)):
            distances = None
        axes = {a.axis: a for a in distances.axes} if distances is not None else {}
        self.direction.configure(text='CAMERA RELATIVE / ' + ('VALIDATED' if axes else 'UNRELIABLE'),
                                 fg=GOOD if axes else BAD)
        if axes:
            self.distance.configure(text=axes['z'].text, fg=GOOD)
            self.side.configure(text=axes['x'].text, fg=GOOD)
            self.height.configure(text=axes['y'].text, fg=GOOD)
            self.source.configure(text=f'DEPTH: {offset.fingertip_source}; target {offset.target_point}\n{offset.tip_check}')
        else:
            estimated = distances is not None and distances.estimated
            preview = estimate_preview(offset, distances, fresh, state == 'LOCKED')
            if preview:
                self.direction.configure(text=f"CAMERA RELATIVE / {preview['qualifier']} ONLY", fg=WARN)
                self.distance.configure(text=preview['z'] + '\n' + preview['qualifier'], fg=WARN)
                self.side.configure(text=preview['x'], fg=WARN)
                self.height.configure(text=preview['y'], fg=WARN)
            elif not self.details:
                self.distance.configure(text='FORWARD: -', fg=DIM)  # clean view: no alarming wording
                self.side.configure(text='LEFT / RIGHT: -', fg=DIM)
                self.height.configure(text='UP / DOWN: -', fg=DIM)
            else:
                self.distance.configure(text='FORWARD: ' + ('ESTIMATED\nUNRELIABLE' if estimated else 'UNRELIABLE'), fg=BAD)
                self.side.configure(text='LEFT / RIGHT: -', fg=DIM)
                self.height.configure(text='UP / DOWN: -', fg=DIM)
            reason = ('frame stale (300 ms limit)' if not fresh else distances.reason if distances is not None
                      else 'no locked target, hand or synchronized depth')
            self.source.configure(text=f'NOT FOR MOVEMENT: {reason}')
        raw = offset if fresh and state == 'LOCKED' else None
        self._values('FINGER', raw.fingertip_m if raw else None, '{:.1f}')
        self._values('TARGET', raw.target_m if raw else None, '{:.1f}')
        self._values('DIFF', raw.delta_m if raw else None)
        self.total.configure(text=('ESTIMATED / UNVALIDATED' if raw and raw.fingertip_estimated else
                                   'UNVALIDATED' if not axes else 'VALIDATED') + ' camera XYZ in cm; '
                             + (f'total {raw.distance_m * 100:.1f} cm' if raw and raw.distance_m is not None else 'total -'))
        self.confidence.configure(
            text=('DEPTH CONFIDENCE: finger {} / target {}'.format(offset.fingertip_confidence, offset.target_confidence)
                  if offset is not None else 'DEPTH CONFIDENCE: UNAVAILABLE'))
        align = data.get('alignment') if fresh else None
        self.pixels.configure(text='2D OFFSET: -' if align is None else
                              f"2D OFFSET (pixels, not cm): dx {align['dx_px']:+.0f} px, dy {align['dy_px']:+.0f} px"
                              + ('  | fingertip inside box' if align['inside_box'] else ''))
        depth = data.get('depth_view') if fresh else None
        if depth is not None:
            from PIL import ImageTk
            self.depth_photo = ImageTk.PhotoImage(depth)
            self.depth.configure(image=self.depth_photo)
        elif self.depth_photo is not None:
            self.depth.configure(image='')
            self.depth_photo = None


def depth_view(frame, box, tip, size=(320, 240)):
    """Colour depth map of the aligned frame (invalid samples black) with the target box and fingertip marked."""
    import cv2
    import numpy as np
    from PIL import Image
    depth = np.asarray(frame.depth, np.float32)
    valid = np.isfinite(depth) & (depth > .12)
    scaled = np.clip((depth - .15) / 1.35, 0, 1)
    colored = cv2.applyColorMap((255 - scaled * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    colored[~valid] = 0
    if box is not None:
        x0, y0, x1, y1 = map(int, box)
        cv2.rectangle(colored, (x0, y0), (x1, y1), (90, 230, 60), 3)
    if tip is not None:
        cv2.circle(colored, tuple(map(int, tip)), 8, (255, 255, 255), 3)
    image = Image.fromarray(cv2.cvtColor(colored, cv2.COLOR_BGR2RGB))
    image.thumbnail(size)
    return image
