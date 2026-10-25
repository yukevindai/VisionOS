"""Offline ALL OBJECTS smoke: the real TrackingWorker on a recorded PathOn frame with SIMULATED camera motion.

Real YOLO-World, ViTTrack lock and (optionally) MediaPipe hands run on the recorded capture, slowly panned and zoomed.
Gemini is SIMULATED: the capture's own saved Gemini labels, with their boxes moved by the measured warp of the frame
that is sent, returned after a fixed delay (default 7 s, inside the 5-9 s measured live). Compares, per request,
request -> verified lock for an already verified inventory object against the HYBRID flow, and the tracking-loop FPS
with and without discovery. Evidence for the pipeline, not a live-camera acceptance test.

    python -m r3_vision.allobjects_replay            # DeskGuide venv (Ultralytics)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import statistics
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from . import gemini
from .all_objects import camera_motion, small_gray

CAPTURE = Path(__file__).resolve().parents[1] / 'recordings/r3-tracking/track-d6a610872f2d4c46b8ed06077160928e'


def warp(image, t):
    """Slow pan and zoom about the centre: +-70 px horizontally, +-30 px vertically, scale 0.95-1.05."""
    h, w = image.shape[:2]
    A = cv2.getRotationMatrix2D((w / 2, h / 2), 0, 1 + .05 * np.sin(2 * np.pi * t / 11))
    A[:, 2] += (70 * np.sin(2 * np.pi * t / 8), 30 * np.sin(2 * np.pi * t / 6))
    return cv2.warpAffine(image, A, (w, h), borderMode=cv2.BORDER_REPLICATE)


class RecordedSource:
    """Latest-only buffer like live_camera.Source, filled at `fps` from the warped recording in its own thread."""
    fps = 20.
    base = None
    status = 'SIMULATED recorded source'

    def __init__(self, args):
        self.latest = None
        self.stop = threading.Event()
        self.started = time.monotonic()
        threading.Thread(target=self.run, daemon=True, name='r3-recorded-source').start()

    def run(self):
        n = 0
        while not self.stop.is_set():
            now = time.monotonic()
            frame = Image.fromarray(warp(self.base, now - self.started))
            n += 1
            self.latest = (frame, time.monotonic(), {'sourceFrameID': n, 'sensorTimestamp': None, 'connectionEpoch': 1})
            self.stop.wait(max(0., 1 / self.fps - (time.monotonic() - now)))

    def snapshot(self):
        return self.latest

    def preview_snapshot(self):
        return self.latest

    def close(self):
        self.stop.set()


def simulated_gemini(base, result, delay, calls):
    """Recorded Gemini labels; boxes follow the sent frame's warp (estimated from its pixels). SIMULATED."""
    controls = result['screenModel']['controls']
    base_gray = small_gray(Image.fromarray(base))

    def transport(url, headers, body, timeout):
        started = time.monotonic()
        payload = json.loads(body)
        jpeg = next(p['inline_data']['data'] for p in payload['contents'][0]['parts'] if 'inline_data' in p)
        sent = Image.open(io.BytesIO(base64.b64decode(jpeg))).convert('RGB')
        gray = small_gray(sent)
        A = camera_motion(base_gray, gray)
        h, w = gray.shape
        elements = []
        for c in controls:
            r = c['region']
            corners = np.float32([[r['x'] * w, r['y'] * h], [(r['x'] + r['width']) * w, (r['y'] + r['height']) * h]])
            if A is not None:
                corners = corners @ np.float32(A)[:, :2].T + np.float32(A)[:, 2]
            (x0, y0), (x1, y1) = corners.clip(0, [w, h])
            elements.append({'label': c['label'], 'legibility': c.get('legibility', 'clear'), 'type': 'other',
                             'box_2d': [round(y0 / h * 1000), round(x0 / w * 1000), round(y1 / h * 1000),
                                        round(x1 / w * 1000)]})
        reply = {'panelType': 'other', 'availability': 'available', 'isPaymentScreen': False, 'sections': [],
                 'elements': elements}
        calls.append(dict(at=round(started, 2), warpFound=A is not None,
                          purpose='verify' if 'other common names' in payload['contents'][0]['parts'][0]['text']
                          else 'request'))
        time.sleep(max(0., delay - (time.monotonic() - started)))
        envelope = {'candidates': [{'content': {'parts': [{'text': json.dumps(reply)}]}}]}
        return 200, json.dumps(envelope).encode()
    return transport


def run_mode(mode, requests, base, result, delay, seconds, output, hands, warmup, extra=()):
    from .live_tracking import TrackingWorker, arguments
    RecordedSource.base = base
    calls = []
    client = gemini.GeminiClient(api_key='SIMULATED', model='replay', live=False,
                                 transport=simulated_gemini(base, result, delay, calls))
    client.prompt = gemini.OBJECT_PROMPT
    client.timeout_s = 60
    detection, _, source = mode.partition(':')  # e.g. allobjects:yoloe
    args = arguments(['--video', 'SIMULATED', '--detection', detection, '--per-minute', '8', '--max-requests', '20',
                      '--timeout', '60', '--hands' if hands else '--no-hands', '--no-audio',
                      '--discovery-source', source or 'world', *extra])
    args.output = output / mode.replace(':', '-')
    report = dict(mode=mode, geminiDelayS=delay, requests=[])
    with patch('r3_vision.live_tracking.Source', RecordedSource):
        worker = TrackingWorker(args, client)
        try:
            deadline = time.monotonic() + 60
            detectors = [worker.detector, getattr(worker.all_objects, 'detector', None), getattr(worker, 'demo', None)]
            while any(d is not None and not d.ready.is_set() for d in detectors) and time.monotonic() < deadline:
                time.sleep(.1)
            time.sleep(warmup)
            report['fpsIdle'] = sample(worker, 6)
            if worker.all_objects is not None:
                report['inventoryBeforeRequests'] = worker.all_objects.summary()
                report['idsBefore'] = ids(worker.all_objects.inventory)
            for ticket, query in enumerate(requests, 1):
                previous = worker.latency.current
                worker.commands.put((ticket, query))
                while worker.latency.current is previous:
                    time.sleep(.01)  # the worker loop has not taken the request yet
                started = time.monotonic()
                while time.monotonic() - started < 30 and (worker.latency.current or {}).get('verifiedS') is None:
                    if worker.session.state in ('AMBIGUOUS', 'UNCERTAIN', 'TARGET LOST', 'ERROR') and \
                            time.monotonic() - started > delay + 2:
                        break
                    time.sleep(.05)
                current = dict(worker.latency.current or {})
                current.pop('startedAt', None)
                report['requests'].append(dict(current, finalState=worker.session.state,
                                               detail=worker.session.detail[:160]))
                if current.get('verifiedS') is not None:
                    report['requests'][-1]['fpsLocked'] = sample(worker, seconds)
            if worker.all_objects is not None:
                inv = worker.all_objects
                report['inventoryAfter'] = inv.summary()
                report['discovery'] = inv.record()
                report['trackIdsCreated'] = inv.inventory.next_id - 1
                report['idsAfter'] = ids(inv.inventory)
        finally:
            worker.close()
    report['geminiCalls'] = calls
    return report


def ids(inventory):
    return {t.id: f'{t.shown_label()} [{t.shown_status()}]' for t in sorted(inventory.visible(), key=lambda t: t.id)}


def sample(worker, seconds):
    values, process = [], []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(.25)
        values.append(worker.fps)
        if worker.process_ms is not None:
            process.append(worker.process_ms)
    return dict(fps=round(statistics.mean(values), 2), fpsMin=round(min(values), 2),
                processMsMedian=round(statistics.median(process), 1) if process else None,
                state=worker.session.state)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--capture', type=Path, default=CAPTURE)
    p.add_argument('--gemini-delay', type=float, default=7.)
    p.add_argument('--seconds', type=float, default=8., help='FPS sampling window while locked')
    p.add_argument('--hands', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--modes', default='hybrid,allobjects')
    p.add_argument('--requests', default='Find the Red Bull can;Find the Pepsi can;Find the bagel',
                   help='Semicolon-separated requests')
    p.add_argument('--discovery-threads', type=int, help='Passed to ALL OBJECTS (default: live tool default)')
    p.add_argument('--discovery-interval', type=float, help='Passed to ALL OBJECTS (default: live tool default)')
    p.add_argument('--report', type=Path, default=Path(__file__).resolve().parents[1] / '.runtime/allobjects-replay.json')
    args = p.parse_args(argv)
    cv2.setNumThreads(1)
    from .tracker_models import ensure_model
    ensure_model(download=False)
    base = np.asarray(Image.open(args.capture / 'captured.png').convert('RGB'))
    result = json.loads((args.capture / 'result.json').read_text(encoding='utf-8'))
    requests = args.requests.split(';')
    output = Path(tempfile.mkdtemp(prefix='allobjects-replay-'))
    reports = []
    for mode in args.modes.split(','):
        # ALL OBJECTS gets time for discovery plus one background Gemini check before the first request.
        warmup = args.gemini_delay + 6 if mode.startswith('allobjects') else 3
        extra = ['--discovery-threads', str(args.discovery_threads)] if args.discovery_threads is not None else []
        if args.discovery_interval is not None:
            extra += ['--discovery-interval', str(args.discovery_interval)]
        reports.append(run_mode(mode, requests, base, result, args.gemini_delay, args.seconds, output,
                                args.hands, warmup, extra))
        reports[-1].update(discoveryThreads=args.discovery_threads, discoveryInterval=args.discovery_interval)
        print(json.dumps(reports[-1], indent=1), flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(dict(capture=args.capture.name, simulated=True, reports=reports), indent=1),
                           encoding='utf-8')
    print('report:', args.report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
