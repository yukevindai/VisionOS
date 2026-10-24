"""Large, high-contrast spatial readout beside the live video (Tk widgets, so the video is never covered).

Shows only values the worker measured for the current frame. Without accepted LiDAR depth it says 3D UNAVAILABLE and
shows the 2D offset in pixels, never as centimetres. A stale frame clears every value and says guidance stopped.
"""
from __future__ import annotations

import time
import tkinter as tk

BG, FG, DIM = '#101418', '#f2f5f7', '#9aa6b2'
GOOD, WARN, BAD, CUE = '#5ee07a', '#ffb347', '#ff6b6b', '#ffe14d'
STATE_COLORS = {'LOCKED': GOOD, 'UNCERTAIN': WARN, 'HIDDEN': WARN, 'REACQUIRING': WARN}


class SpatialPanel:
    def __init__(self, parent, width=520):
        self.frame = tk.Frame(parent, bg=BG, width=width, padx=14, pady=10)
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
        self.direction = label('DIRECTION: -', 30, CUE, True, row=2)
        self.distance = label('3D UNAVAILABLE', 60, BAD, True, row=3)
        label('cm', 18, DIM, row=4, column=0, columnspan=1)
        self.cells = {}
        for column, axis in enumerate(('X', 'Y', 'Z'), 1):
            label(axis, 22, DIM, True, 'Consolas', row=4, column=column, columnspan=1)
        for row, name in enumerate(('FINGER', 'TARGET', 'DIFF'), 5):
            label(name, 18, DIM, True, row=row, column=0, columnspan=1)
            for column in range(1, 4):
                self.cells[name, column] = label('-', 30, FG, True, 'Consolas', row=row, column=column,
                                                 columnspan=1, padx=(10, 0))
        label('Axes as seen by the phone: X+ right, Y+ down, Z+ away from the phone', 15, DIM, row=8)
        self.source = label('DEPTH SOURCE: -', 16, DIM, row=14, pady=(8, 0))
        self.confidence = label('DEPTH CONFIDENCE: -', 20, FG, True, row=9)
        self.pixels = label('2D OFFSET: -', 18, DIM, row=10)
        self.timing = label('FRAME AGE: -', 20, FG, True, row=11, pady=(6, 0))
        self.audio = label('AUDIO: OFF', 16, DIM, row=12, pady=(6, 0))
        self.depth = tk.Label(self.frame, bg=BG)
        self.depth.grid(row=15, column=0, columnspan=4, sticky='w', pady=(8, 0))

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
        self.audio.configure(text=(f"AUDIO: {'ON' if audio.get('enabled') else 'OFF'}"
                                   + (f"  |  LAST CUE: {audio.get('last_cue') or '-'}  |  {audio.get('playback') or '-'}"
                                      f"\nSUPPRESSED: {audio.get('suppressed') or '-'}" if audio.get('enabled') else '')))
        offset = data.get('offset') if fresh else None
        measured = offset is not None and offset.measured and offset.delta_m is not None
        cue, mode = data.get('direction') if fresh else (None, None)
        self.direction.configure(text=f"DIRECTION: {cue.replace('_', ' ') if cue else '-'}"
                                      + (f'  ({mode})' if cue and mode else ''),
                                 fg=CUE if cue else DIM)
        if not fresh:
            self.distance.configure(text='FRAME STALE', fg=BAD)
            self.source.configure(text='Guidance stopped: no fresh frame (300 ms limit)')
        elif measured:
            self.distance.configure(text=f'{offset.distance_m * 100:.1f} cm', fg=GOOD)
            self.source.configure(text=f'DEPTH SOURCE: finger {offset.fingertip_source}; target LiDAR '
                                       f'{offset.target_point}\n{offset.tip_check}')
        else:
            reason = offset.detail if offset is not None else 'no locked target, hand or synchronized depth'
            self.distance.configure(text='3D UNAVAILABLE', fg=BAD)
            self.source.configure(text=f'DEPTH SOURCE: {reason[:90]}')
        self._values('FINGER', offset.fingertip_m if measured else None, '{:.1f}')
        self._values('TARGET', offset.target_m if measured else None, '{:.1f}')
        self._values('DIFF', offset.delta_m if measured else None)
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
        elif self.depth_photo is not None and not fresh:
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
