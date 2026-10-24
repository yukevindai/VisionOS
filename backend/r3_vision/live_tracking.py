"""Windows live 2D target tracking. Frozen debug tool remains r3_vision.live_camera."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from PIL import ImageDraw, ImageTk

from .cli import client_from_args
from .live_camera import Source, upright, process_capture
from .tracking_session import RequestBudget, TrackingSession
from .visual_tracker import working_image
from .frame_history import FrameHistory
from .frame_packet import make_packet, TargetOverlay
from .alignment import draw_alignment, draw_line, fingertip_alignment

BOX_COLORS = {'LOCKED': (60, 230, 90), 'UNCERTAIN': (255, 170, 0)}


class TrackingWorker:
    """Serial local CV worker plus at most one cloud worker. Latest-only UI buffer."""
    def __init__(self, args, client):
        self.args, self.client = args, client
        self.source = Source(args)
        self.factory = None
        self.history = None
        if getattr(args, 'tracker', 'orb') == 'vit':
            from .hybrid_tracker import HybridTracker
            self.factory = HybridTracker
            self.history = FrameHistory()
        budget = RequestBudget(args.cooldown, args.per_minute, args.max_requests)
        if getattr(args, 'tracker', 'orb') == 'lock':
            from .lock_session import LockSession
            self.session = LockSession(budget, args.reidentify_after, args.reference_ttl)
        else:
            self.session = TrackingSession(budget, args.reidentify_after, args.reference_ttl,
                                           **({'tracker_factory': self.factory} if self.factory else {}))
        self.fps = 0.
        self.alignment = None
        self.log = self.log_path = None
        self.hand_pool = None
        self.spatial = None
        self.offset = None
        self.process_ms = None
        self.previous_received = None
        self.track_edge = getattr(args, 'track_edge', None) or (640 if getattr(args, 'tracker', 'orb') == 'lock' else 960)
        self.logged = 0
        self.commands = queue.Queue()
        self.responses = queue.Queue()
        self.stopped = threading.Event()
        self.output = None
        self.stream_id = uuid.uuid4().hex
        self.latest_frame = None
        self.latest_target = None
        self.latest_hand = None
        self.hand_bridge = None
        self.hand_status = 'INITIALIZING' if getattr(args, 'hands', False) else 'OFF'
        self.ticket = 0
        self.thread = threading.Thread(target=self.run, daemon=True, name='r3-local-tracker')
        self.thread.start()

    def identify(self, job, raw, info):
        directory = self.args.output / job.frame_id
        metadata = dict(info, frameID=job.frame_id, snapshotUTCms=int(time.time()*1000),
                        receivedMonotonic=job.received, snapshotMonotonic=time.monotonic(),
                        desktopROI=list(self.args.roi) if self.args.roi else None,
                        sourceKind='REC3D' if self.args.host else 'video')
        prepared = None
        try:
            process_capture(raw, self.args.rotation, job.query, metadata, self.client, directory)
            result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
            if getattr(self.args, 'target_kind', 'control') == 'object' and result.get('screenModel'):
                from . import object_select
                selection = object_select.select(job.query, result['screenModel'])
                if selection.get('status') == 'AMBIGUOUS':
                    selection['candidateLabels'] = object_select.candidates(selection, result['screenModel'])
                result['selection'] = selection
                (directory/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            if self.factory and result['selection']['status'] == 'SELECTED':
                prepared = self.factory(job.image, result['selection']['target']['imageRegion'])
                replay_ok, replay_count = prepared.prime(self.history, job.received, job.epoch)
                (directory/'tracking-init.json').write_text(json.dumps({
                    'algorithm': 'OpenCV ViTTrack + SIFT recovery', 'replaySucceeded': replay_ok,
                    'replayedFrames': replay_count, 'historyBytesLimit': self.history.max_bytes,
                    'historySecondsLimit': self.history.seconds}), encoding='utf-8')
        except Exception as exc:
            result = {'provider': {'ok': False, 'error': {'kind': f'LOCAL_PROCESSING_ERROR_{type(exc).__name__}'}},
                      'selection': {'status': 'PROVIDER_UNAVAILABLE'}, 'mode': '', 'timingMs': {}}
        self.responses.put((job, result, prepared))

    def run(self):
        last_frame = None
        last_state = None
        last_log = 0.
        frames = []
        if getattr(self.args, 'hands', False):
            try:
                from .hand_bridge import HandBridge
                self.hand_bridge = HandBridge()
                self.hand_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='r3-hands')
                if self.args.host and getattr(self.args, 'tracker', 'orb') == 'lock':
                    from .spatial import SpatialOffset
                    self.spatial = SpatialOffset()
            except Exception as exc:
                self.hand_status = f'ERROR ({type(exc).__name__}); check MediaPipe/model installation'
        while not self.stopped.is_set():
            try:
                while True:
                    ticket, query = self.commands.get_nowait()
                    self.ticket = ticket
                    self.session.find(query)
                    if self.spatial is not None:
                        self.spatial.reset()
                    self.latest_frame = self.latest_target = self.latest_hand = None
                    last_frame = None
            except queue.Empty:
                pass
            try:
                while True:
                    job, result, prepared = self.responses.get_nowait()
                    try:
                        self.session.complete(job, result, time.monotonic(), prepared_tracker=prepared)
                    except Exception as exc:
                        self.session.inflight = None
                        self.session.tracker = None
                        self.session.state = 'ERROR'
                        self.session.detail = f'Reference verification failed ({type(exc).__name__}); no box'
                        self.session.lost_at = time.monotonic()
            except queue.Empty:
                pass
            try:
                item = self.source.snapshot()
                now = time.monotonic()
                if item is not None:
                    raw, received, info = item
                    epoch = (info.get('connectionEpoch'), raw.size, self.args.rotation)
                    self.session.source_epoch(epoch)
                    # Do not count a repeated buffer as a second confirmation frame.
                    if received != last_frame:
                        started = time.monotonic()
                        image = upright(raw, self.args.rotation)
                        if self.history is not None and self.session.active:
                            self.history.append(image, received, epoch)
                        packet = make_packet(raw, received, info, self.stream_id, self.args.rotation,
                                             max_edge=self.track_edge, upright_image=image)
                        self.latest_frame = packet
                        display = packet.image.copy()
                        hand_future = (self.hand_pool.submit(self.hand_bridge.process, packet)
                                       if self.hand_bridge is not None else None)
                        match = self.session.update(packet.image, received, now)
                        polygon = tuple((float(p[0])/display.width, float(p[1])/display.height)
                                        for p in match.polygon) if match.polygon is not None and time.monotonic()-received <= .3 else None
                        self.latest_target = TargetOverlay(packet.stamp, self.session.generation, self.session.state, self.session.label,
                            polygon, getattr(self.session.tracker, 'last_score', None),
                            match.correlation if polygon is not None else None)
                        if match.polygon is not None and time.monotonic()-received <= .3:
                            color = BOX_COLORS.get(self.session.state, 'red')
                            draw = ImageDraw.Draw(display)
                            draw.line([tuple(map(float, p)) for p in match.polygon] +
                                      [tuple(map(float, match.polygon[0]))], fill=color, width=3)
                            x0, y0 = (float(v) for v in match.polygon.min(axis=0))
                            draw.text((x0, max(0., y0-14)), f'{self.session.label} {self.session.state}', fill=color)
                        self.alignment = None
                        self.offset = None
                        if hand_future is not None:
                            try:
                                hand = hand_future.result()
                                display = self.hand_bridge.draw(display, packet, hand, time.monotonic())
                                self.latest_hand = hand
                                self.hand_status = f'{hand.result.status} ({len(hand.result.hands)} hands)'
                                primary = hand.result.primary
                                if polygon is not None and primary is not None and primary.index_tip_in_frame:
                                    self.alignment = fingertip_alignment(match.polygon, primary.index_tip_px, display.size)
                                    self.hand_status += ' | ' + draw_alignment(display, self.alignment, primary.index_tip_px)
                                    if self.spatial is not None:
                                        try:
                                            rgbd = getattr(self.source, 'last_rgbd', None)
                                            rgbd = rgbd if rgbd is not None and rgbd.received == received else None
                                            aligned = self.spatial.aligned(rgbd, display.size, self.args.rotation)
                                            self.offset = self.spatial.measure(aligned, match.polygon,
                                                                               primary.landmarks_px, received)
                                            line = self.offset.text()
                                        except Exception as exc:
                                            self.offset = None
                                            line = f'3D: unavailable ({type(exc).__name__})'
                                        self.hand_status += ' | ' + draw_line(display, line, 2,
                                            (60, 230, 90) if self.offset is not None and self.offset.measured
                                            else (255, 190, 60))
                            except Exception as exc:
                                self.latest_hand = None
                                self.hand_status = f'ERROR ({type(exc).__name__}); hand overlay hidden'
                        frames.append(time.monotonic())
                        while frames and frames[-1]-frames[0] > 2:
                            frames.pop(0)
                        self.fps = (len(frames)-1)/(frames[-1]-frames[0]) if len(frames) > 1 and frames[-1] > frames[0] else 0.
                        new_job = self.session.job(image, received, time.monotonic())
                        if new_job is not None:
                            threading.Thread(target=self.identify, args=(new_job, raw.copy(), dict(info)),
                                             daemon=True, name='r3-identify').start()
                        self.output = (self.ticket, display, received, self.session.state,
                                       self.session.describe(time.monotonic())+' | Hands: '+self.hand_status
                                       + f' | {self.fps:.1f} FPS | frame age {(time.monotonic()-received)*1000:.0f} ms',
                                       self.session.mode, self.session.budget.total, packet.image)
                        self.process_ms = (time.monotonic()-started)*1000
                        self._log(received, match, display.size)
                        self.previous_received = received
                        last_frame = received
                    elif now-received > .3:
                        self.latest_frame = self.latest_target = self.latest_hand = None
                        self.session.update(working_image(raw), received, now)
                        self.output = (self.ticket, None, received, 'TARGET LOST', 'Camera frame stale; box hidden',
                                       self.session.mode, self.session.budget.total)
                else:
                    self.latest_frame = self.latest_target = self.latest_hand = None
                    self.session.update(None, now-1, now)
                    self.output = (self.ticket, None, now, 'TARGET LOST', self.source.status,
                                   self.session.mode, self.session.budget.total)
                state = (self.session.state, self.session.detail)
                if state != last_state and (last_state is None or state[0] != last_state[0]
                                             or time.monotonic()-last_log >= 1):
                    # State/detail are local or sanitized pipeline categories, never endpoint/key values.
                    print(f'{self.session.state}: {self.session.detail}', flush=True)
                    last_state = state
                    last_log = time.monotonic()
            except Exception as exc:
                self.latest_frame = self.latest_target = self.latest_hand = None
                self.session.tracker = None
                self.session.hits = 0
                self.session.state, self.session.detail = 'ERROR', f'Local tracker failure ({type(exc).__name__}); box hidden'
                self.output = (self.ticket, None, time.monotonic(), 'ERROR', self.session.detail,
                               self.session.mode, self.session.budget.total)
            self.stopped.wait(.005 if getattr(self.args, 'tracker', 'orb') == 'lock' else .07)
        if self.hand_pool is not None:
            self.hand_pool.shutdown(wait=True)
        if self.hand_bridge is not None:
            self.hand_bridge.close()

    def _log(self, received, match, size):
        if self.log is None:
            self.args.output.mkdir(parents=True, exist_ok=True)
            self.log_path = self.args.output / f'session-{self.stream_id[:12]}.jsonl'
            self.log = self.log_path.open('a', encoding='utf-8')
        tracker = self.session.tracker
        polygon = getattr(match, 'polygon', None)
        record = dict(t=round(time.monotonic(), 3), frameAgeMs=round((time.monotonic()-received)*1000, 1),
                      fps=round(self.fps, 1), state=self.session.state, reason=getattr(match, 'reason', ''),
                      box=None if polygon is None else [round(float(v), 1) for v in (*polygon.min(0), *polygon.max(0))],
                      imageSize=list(size), trackerScore=getattr(tracker, 'last_score', None),
                      colors=getattr(tracker, 'last_appearance', None),
                      featureCheckFailures=getattr(tracker, 'verify_failures', None),
                      keyframes=len(getattr(tracker, 'keyframes', []) or []),
                      hands=self.hand_status.split(' | ')[0], fingertipInsideBox=None if self.alignment is None
                      else self.alignment['inside_box'], apiRequests=self.session.budget.total,
                      processMs=None if self.process_ms is None else round(self.process_ms, 1),
                      receivedGapMs=None if self.previous_received is None else round((received-self.previous_received)*1000, 1),
                      offset3d=None if self.offset is None else self.offset.record())
        self.log.write(json.dumps(record)+'\n')
        self.logged += 1
        if self.logged % 15 == 0:
            self.log.flush()

    def close(self):
        self.stopped.set()
        self.source.close()
        self.thread.join(timeout=2)
        if self.log is not None:
            self.log.close()


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--host', help='Current user-supplied PathOn REC3D host')
    src.add_argument('--video', help='Explicit OpenCV camera index, URL, or test video')
    p.add_argument('--port', type=int)
    p.add_argument('--rotation', type=int, choices=[0, 90, 180, 270], default=0)
    p.add_argument('--timeout', type=float, default=60)
    p.add_argument('--tracker', choices=['lock', 'vit', 'orb'], default='lock',
                   help='Object lock: ViTTrack + object-only SIFT identity (default); previous ViTTrack hybrid; old ORB debugger')
    p.add_argument('--target-kind', choices=['object', 'control'], default='object',
                   help='Gemini prompt for ordinary objects (default) or panel/touchscreen controls')
    p.add_argument('--thinking', choices=['low', 'medium', 'high'], default='low',
                   help='Gemini 3 thinking level for live identification; high measured 22-30 s per request')
    p.add_argument('--hands', action=argparse.BooleanOptionalAction, default=True,
                   help="Show Claude's hand module on the identical frame (default); --no-hands disables it")
    p.add_argument('--cooldown', type=float, default=20)
    p.add_argument('--per-minute', type=int, default=3)
    p.add_argument('--max-requests', type=int, default=20)
    p.add_argument('--reidentify-after', type=float, help='Continuous loss before cloud retry, seconds (lock 5, others 2)')
    p.add_argument('--reference-ttl', type=float, default=60, help='Maximum local reference lifetime, seconds')
    p.add_argument('--request', default='Find the Red Bull can')
    p.add_argument('--replay', type=Path, help='SIMULATED fixed provider reply; no API calls')
    p.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'recordings/r3-tracking')
    args = p.parse_args(argv)
    args.roi = None  # Existing Source interface; ROI remains in the frozen debugging tool.
    if args.reidentify_after is None:
        args.reidentify_after = 5. if args.tracker == 'lock' else 2.
    if args.host and (args.port is None or not 1 <= args.port <= 65535):
        p.error('--host requires current REC3D --port (1..65535)')
    for name in ['timeout', 'cooldown', 'reidentify_after', 'reference_ttl']:
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            p.error(f'--{name.replace("_", "-")} must be finite and positive')
    if args.per_minute < 1 or args.max_requests < 1:
        p.error('API request limits must be positive integers')
    return args


def main(argv=None):
    args = arguments(argv)
    if sys.platform != 'win32':
        raise SystemExit('Interactive camera tool is Windows-only')
    import cv2
    cv2.setNumThreads(1)
    if args.tracker in ('lock', 'vit'):
        if not hasattr(cv2, 'TrackerVit_create'):
            raise SystemExit('This OpenCV build lacks ViTTrack. Install backend/requirements-live.txt into this Python environment.')
        from .tracker_models import ensure_model
        ensure_model(download=True)
    client = client_from_args(args.replay)
    client.timeout_s = args.timeout
    if args.target_kind == 'object':
        from .gemini import OBJECT_PROMPT
        client.prompt = OBJECT_PROMPT
    if client.live and client.model.startswith('gemini-3'):
        client.thinking_level = args.thinking
    if client.live and not client.configured:
        raise SystemExit('Set GEMINI_API_KEY and GEMINI_MODEL in environment or backend/.env; no request sent')
    import tkinter as tk
    root = tk.Tk()
    root.title('Reflex R3 LIVE 2D tracking - NO GUIDANCE')
    window_width = max(320, min(1000, root.winfo_screenwidth()-80))
    window_height = max(300, min(760, root.winfo_screenheight()-100))
    root.geometry(f'{window_width}x{window_height}')
    root.minsize(min(480, window_width), min(320, window_height))
    root.columnconfigure(0, weight=1)
    root.rowconfigure(3, weight=1)
    toolbar = tk.Frame(root)
    toolbar.grid(row=0, column=0, sticky='ew', padx=8, pady=6)
    toolbar.columnconfigure(0, weight=1)
    query = tk.StringVar(value=args.request)
    tk.Entry(toolbar, textvariable=query).grid(row=0, column=0, sticky='ew', padx=(0, 8))
    status = tk.StringVar(value='SEARCHING: enter request and click Find & Track')
    status_label = tk.Label(root, textvariable=status, wraplength=window_width-24)
    status_label.grid(row=1, column=0, sticky='ew', padx=8)
    mode = tk.StringVar(value='SIMULATED' if args.replay else 'REAL_API on demand; no movement outputs')
    tk.Label(root, textvariable=mode).grid(row=2, column=0, sticky='ew', padx=8)
    viewport = tk.Frame(root)
    viewport.grid(row=3, column=0, sticky='nsew', padx=8, pady=6)
    # Image dimensions must never enlarge the window or push controls off-screen.
    viewport.grid_propagate(False)
    viewport.pack_propagate(False)
    preview = tk.Label(viewport, text='LIVE VIDEO - waiting for current frame', compound='top')
    preview.pack(expand=True, fill='both')
    worker = TrackingWorker(args, client)
    ticket = 0
    last = None
    last_size = None

    def find():
        nonlocal ticket, last
        ticket += 1
        last = None
        preview.configure(image='', text='SEARCHING - waiting for new target identification')
        preview.image = None
        status.set('SEARCHING: waiting for fresh frame / previous cloud request to finish')
        worker.commands.put((ticket, query.get()))

    tk.Button(toolbar, text='Find & Track', command=find).grid(row=0, column=1, padx=(0, 8))

    def stop():
        nonlocal ticket, last
        ticket += 1
        last = None
        worker.commands.put((ticket, ''))
        preview.configure(image='', text='SEARCHING - stopped; no box')
        preview.image = None
        status.set('SEARCHING: stopped')

    tk.Button(toolbar, text='Stop tracking', command=stop).grid(row=0, column=2)
    root.bind('<Return>', lambda event: find())

    def tick():
        nonlocal last, last_size
        item = worker.output
        if item and item[0] == ticket:
            _, image, received, state, detail, source_mode, requests = item[:7]
            clean = item[7] if len(item) > 7 else None
            age = time.monotonic()-received
            fresh = 0 <= age <= .3
            delayed = not fresh and clean is not None and 0 <= age <= 1.
            size = (max(1, viewport.winfo_width()-12), max(1, viewport.winfo_height()-36))
            status_label.configure(wraplength=max(200, root.winfo_width()-24))
            if image is None or not (fresh or delayed):
                preview.configure(image='', text='TARGET LOST - no fresh verified video frame')
                preview.image = None
                last = None
                status.set(f'{state if image is None else "TARGET LOST"}: {detail if image is None else "Frame stale; box hidden"}')
            elif (item, fresh) != last or size != last_size:
                fitted = (image if fresh else clean).copy()
                fitted.thumbnail(size)
                photo = ImageTk.PhotoImage(fitted)
                caption = (f'LIVE VIDEO | {state} | 2D VISUAL TEST ONLY' if fresh else
                           f'LIVE VIDEO DELAYED {age*1000:.0f} ms | box hidden until a fresh frame')
                preview.configure(image=photo, text=caption)
                preview.image = photo
                status.set(f'{state}: {detail}' if fresh else f'{state}: frame {age*1000:.0f} ms old; box hidden')
                last = (item, fresh)
                last_size = size
            model_info = getattr(client, 'model', '')
            effort = getattr(client, 'thinking_level', '')
            mode.set(f'{source_mode or ("SIMULATED" if args.replay else "REAL_API")} | {model_info} {effort} | API requests: {requests}/{args.max_requests} | No guidance')
        root.after(30, tick)

    def close():
        root.destroy()
        worker.close()
    root.protocol('WM_DELETE_WINDOW', close)
    tick()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
