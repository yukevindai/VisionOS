"""On-demand desktop camera experiment. No tracking or guidance outputs."""
from __future__ import annotations

import argparse
import io
import json
import math
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from . import gemini, pipeline, selector
from .cli import client_from_args


def pixel_box(region: dict, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Normalized upright coordinates -> inclusive PIL pixel bounds; never repair bad boxes."""
    if not selector.region_is_valid(region) or min(size) <= 0:
        raise ValueError("Invalid normalized target box/image size")
    w, h = size
    return (math.floor(region['x'] * w), math.floor(region['y'] * h),
            min(w - 1, math.ceil(round((region['x'] + region['width']) * w, 10)) - 1),
            min(h - 1, math.ceil(round((region['y'] + region['height']) * h, 10)) - 1))


MAX_FRAME_AGE_S = 1.0
LIVE_CAPTION = 'LIVE PREVIEW - no boxes, no analysis'
NO_FRAME_CAPTION = 'LIVE PREVIEW - NO CURRENT FRAME (stream stopped, stale or disconnected)'


def frame_is_current(item, now: float, max_age: float = MAX_FRAME_AGE_S) -> bool:
    """True only for a frame received within max_age on the local monotonic clock."""
    return item is not None and 0 <= now - item[1] <= max_age


def upright(raw: Image.Image, rotation: int) -> Image.Image:
    return raw.rotate(-rotation, expand=True) if rotation else raw.copy()


def raw_point(x: float, y: float, rotation: int) -> tuple[float, float]:
    """Inverse clockwise rotation in continuous normalized edge coordinates."""
    return {0: (x, y), 90: (y, 1-x), 180: (1-x, 1-y), 270: (1-y, x)}[rotation]


def render_result(image: Image.Image, result: dict) -> tuple[Image.Image, str]:
    output = image.copy()
    selection = result['selection']
    status = selection['status']
    target = selection.get('target')
    text = f"{result['mode']} | {status} | Gemini {result['timingMs']['provider']/1000:.2f}s"
    error = (result.get('provider') or {}).get('error') or {}
    if error:
        text += f" | {error.get('kind', 'PROVIDER_ERROR')} (HTTP {error.get('status')})"
    if selection.get('reason'):
        text += f" | {selection['reason']}"
    if status == 'SELECTED' and target:
        model = result['screenModel']
        if (target.get('frameId') != model.get('frameId') or
                model.get('coordinateSystem') != selector.SUPPORTED_COORDINATES or
                model.get('image', {}).get('widthPx') != image.width or
                model.get('image', {}).get('heightPx') != image.height):
            raise ValueError('Result frame/coordinates/dimensions do not match frozen image')
        box = pixel_box(target['imageRegion'], image.size)
        ImageDraw.Draw(output).rectangle(box, outline='red', width=max(2, image.width // 250))
        confidence = target.get('confidence')
        text += f" | {target['label']} | confidence: {confidence if confidence is not None else 'not reported'}"
    return output, text


class Source:
    """Latest-only buffer; acquisition stays separate from UI and Gemini."""
    def __init__(self, args):
        self.args = args
        self.latest = None
        self.status = 'Connecting / waiting for first frame'
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.phone = None
        if args.host:
            # Reuse only the existing R1 transport module, without editing it or loading models.
            prototype = Path(__file__).resolve().parents[2] / 'ios/Packages/Perception/Prototypes/DeskGuide-Laptop'
            sys.path.insert(0, str(prototype))
            from deskguide.stream import Phone
            self.phone = Phone(args.host, args.port)
            self.phone.start()
        else:
            threading.Thread(target=self.run, daemon=True, name='r3-capture').start()

    def run(self):
        import cv2
        cap = None
        try:
            if self.args.video is not None:
                src = self.args.video
                cap = cv2.VideoCapture(int(src) if src.isdecimal() else src)
                if not cap.isOpened():
                    raise ValueError('Cannot open supplied video source; check source and camera permissions')
            while not self.stop.is_set():
                if cap is not None:
                    ok, bgr = cap.read()
                    if not ok:
                        raise ValueError('Video source disconnected or reached end; restart with current source')
                    image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                else:
                    x, y, w, h = self.args.roi
                    image = ImageGrab.grab(bbox=(x, y, x+w, y+h), all_screens=True).convert('RGB')
                received = time.monotonic()
                with self.lock:
                    self.latest = (image, received, {'sourceFrameID': None, 'sensorTimestamp': None})
                self.status = 'Receiving video' if cap is not None else 'Desktop ROI (keep camera window visible)'
                self.stop.wait(0.1)
        except Exception as exc:
            # Do not echo arbitrary URLs/credentials or external exception messages.
            self.status = f'Source failed ({type(exc).__name__}); verify source and restart'
            with self.lock:
                self.latest = None
        finally:
            if cap is not None:
                cap.release()

    def snapshot(self):
        if self.phone:
            f = self.phone.latest
            self.status = ('Receiving PathOn REC3D' if f is not None else 'PathOn unavailable; check host, port, network and streaming')
            if f is None:
                return None
            cached = getattr(self, '_cached', None)
            if cached is not None and cached[0] is f:
                return cached[1]
            import cv2
            item = (Image.fromarray(cv2.cvtColor(f.color, cv2.COLOR_BGR2RGB)), f.received,
                    {'sourceFrameID': f.id, 'sensorTimestamp': f.timestamp, 'connectionEpoch': f.epoch})
            self._cached = (f, item)
            self.last_rgbd = f
            return item
        with self.lock:
            return self.latest

    def close(self):
        self.stop.set()
        if self.phone:
            self.phone.close()


def process_capture(raw, rotation, request, metadata, client, directory, fast=False, runner=None):
    """Save immutable raw + upright input; existing pipeline is the ONLY interpreter/selector.

    fast=True (live tracking): send a JPEG straight away and write the diagnostic PNGs in the background afterwards,
    so about 0.6 s of local encoding is no longer spent before the Gemini request.
    """
    directory.mkdir(parents=True, exist_ok=False)
    image = upright(raw, rotation)
    metadata = dict(metadata, rawSize=list(raw.size), uprightSize=list(image.size),
                    clockwiseRotation=rotation, exifOrientation=1, orientationApplied=True,
                    cropNormalized=[0, 0, 1, 1], mirroring='unchanged; source mirroring unknown',
                    transform='upright = raw rotated clockwise, expanded, no resize or additional crop',
                    captureTimeMeaning='desktop snapshot UTC; sensor exposure UTC unavailable')
    (directory / 'capture.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    if not fast:
        raw.save(directory / 'original.png')
        image.save(directory / 'captured.png')
    encoded = io.BytesIO()
    image.save(encoded, format='JPEG', quality=95) if fast else image.save(encoded, format='PNG')
    result = (runner or pipeline.run)(encoded.getvalue(), request, client, frame_id=metadata['frameID'],
                          captured_at_ms=metadata['snapshotUTCms'])
    (directory / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if fast:
        def save():
            raw.save(directory / 'original.png')
            image.save(directory / 'captured.png')
            try:
                render_result(image, result)[0].save(directory / 'annotated.png')
            except (KeyError, TypeError, ValueError):
                pass
        threading.Thread(target=save, daemon=True, name='r3-capture-files').start()
        return None, ''
    rendered, text = render_result(image, result)
    rendered.save(directory / 'annotated.png')
    return rendered, text


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--host', help='Current PathOn host, entered by user; requires --port')
    group.add_argument('--video', help='Explicit OpenCV video URL, file or camera index')
    group.add_argument('--roi', nargs=4, type=int, metavar=('X', 'Y', 'W', 'H'))
    group.add_argument('--select-roi', action='store_true', help='Select visible camera preview on primary desktop')
    p.add_argument('--port', type=int, help='Current PathOn REC3D port, no default endpoint')
    p.add_argument('--rotation', type=int, choices=[0, 90, 180, 270], default=0)
    p.add_argument('--timeout', type=float, default=30, help='Desktop experiment only; seconds')
    p.add_argument('--request', default='Find floor 5')
    p.add_argument('--replay', type=Path, help='SIMULATED Gemini REST fixture, no API calls')
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'recordings/r3-live')
    args = p.parse_args(argv)
    if sys.platform != 'win32':
        p.error('Interactive preview is Windows-only')
    if args.host and (args.port is None or not 1 <= args.port <= 65535):
        p.error('--host requires the actual PathOn --port (1..65535)')
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120:
        p.error('--timeout must be in (0, 120] seconds')
    if args.roi and min(args.roi[2:]) <= 0:
        p.error('ROI width and height must be positive')
    import cv2
    cv2.setNumThreads(1)
    if args.select_roi:
        import numpy as np
        desktop = cv2.cvtColor(np.array(ImageGrab.grab()), cv2.COLOR_RGB2BGR)
        args.roi = tuple(map(int, cv2.selectROI('Select camera pixels only; Enter accepts, Esc cancels', desktop)))
        cv2.destroyAllWindows()
        if min(args.roi[2:]) <= 0:
            return 0
    client = client_from_args(args.replay)
    client.timeout_s = args.timeout
    if client.live and not client.configured:
        p.error('Set GEMINI_API_KEY and GEMINI_MODEL in environment or backend/.env; no API call made')
    import tkinter as tk
    root = tk.Tk()
    root.title('Reflex R3 camera test - frozen interpretation, NO GUIDANCE')
    request = tk.StringVar(value=args.request)
    tk.Entry(root, textvariable=request, width=70).pack()
    status = tk.StringVar(value='Ready. Edit request, then click Capture or press F5.')
    tk.Label(root, textvariable=status, wraplength=1100).pack()
    panels = tk.Frame(root)
    panels.pack()
    # compound='top' keeps each caption visible above its image; Tk otherwise hides text once an image is set.
    live_label = tk.Label(panels, text=NO_FRAME_CAPTION, compound='top')
    live_label.pack(side='left')
    frozen_label = tk.Label(panels, text='FROZEN CAPTURE - awaiting capture', compound='top')
    frozen_label.pack(side='left')
    source = Source(args)
    results = queue.Queue(maxsize=1)
    busy = False
    frozen = None
    shown = None

    def show(label, image):
        preview = image.copy()
        preview.thumbnail((560, 600))
        photo = ImageTk.PhotoImage(preview)
        label.configure(image=photo)
        label.image = photo

    def capture(event=None):
        nonlocal busy, frozen
        if busy:
            return
        item = source.snapshot()
        if not frame_is_current(item, time.monotonic()):
            status.set('No recently received frame. Check PathOn/source; capture refused.')
            return
        if not request.get().strip():
            status.set('Enter a target request first.')
            return
        raw, received, info = item
        raw = raw.copy()
        frozen = upright(raw, args.rotation)
        captured_at = time.strftime('%H:%M:%S')
        frozen_label.configure(text=f'FROZEN CAPTURE {captured_at} - NOT LIVE - analysing...')
        show(frozen_label, frozen)
        busy = True
        button.configure(state='disabled')
        text = request.get().strip()
        frame_id = f'capture-{uuid.uuid4().hex}'
        directory = args.output / frame_id
        metadata = dict(info, frameID=frame_id, snapshotUTCms=int(time.time()*1000),
                        receivedMonotonic=received, snapshotMonotonic=time.monotonic(),
                        desktopROI=list(args.roi) if args.roi else None,
                        sourceKind='REC3D' if args.host else 'video' if args.video is not None else 'desktopROI')
        status.set('PROCESSING frozen capture; live preview continues. No tracking / no guidance.')

        def work():
            caption = f'FROZEN CAPTURE {captured_at} - NOT LIVE - result is for this still image only'
            try:
                rendered, summary = process_capture(raw, args.rotation, text, metadata, client, directory)
                results.put((rendered, summary + f' | Saved: {directory}', caption))
            except Exception as exc:
                results.put((None, f'Capture/processing failed ({type(exc).__name__}); inspect saved capture, disk permissions and configuration. No target guessed.', caption))
        threading.Thread(target=work, daemon=True, name='r3-gemini-on-demand').start()

    button = tk.Button(root, text='Capture ONE frame (F5)', command=capture)
    button.pack()
    root.bind('<F5>', capture)
    connection = tk.StringVar()
    tk.Label(root, textvariable=connection).pack()

    def tick():
        nonlocal busy, frozen, shown
        item = source.snapshot()
        current = frame_is_current(item, time.monotonic())
        connection.set(source.status + ('' if current else ' | stale/no recent frame'))
        if not current:
            # Never leave the last received frame on screen under a LIVE caption.
            if shown is not None:
                live_label.configure(image='', text=NO_FRAME_CAPTION)
                live_label.image = None
                shown = None
        elif item[1] != shown:
            live_label.configure(text=LIVE_CAPTION)
            show(live_label, upright(item[0], args.rotation))
            shown = item[1]
        try:
            rendered, text, caption = results.get_nowait()
            frozen_label.configure(text=caption)
            if rendered is not None:
                frozen = rendered
                show(frozen_label, frozen)
            status.set(text)
            busy = False
            button.configure(state='normal')
        except queue.Empty:
            pass
        root.after(100, tick)

    def close():
        root.destroy()
        source.close()
    root.protocol('WM_DELETE_WINDOW', close)
    tick()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
