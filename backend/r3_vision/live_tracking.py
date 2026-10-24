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

from . import gemini
from .cli import client_from_args
from .live_camera import Source, upright, process_capture
from .tracking_session import RequestBudget, TrackingSession
from .visual_tracker import working_image
from .frame_history import FrameHistory
from .frame_packet import make_packet, TargetOverlay
from .alignment import draw_alignment, draw_line, fingertip_alignment

BOX_COLORS = {'LOCKED': (60, 230, 90), 'UNCERTAIN': (255, 170, 0), 'HIDDEN': (255, 140, 0)}


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
        self.audio = None
        self.panel = None
        self.depth_frame = None
        self.audio_state = dict(enabled=bool(getattr(args, 'audio', False)), last_cue=None, playback=None,
                                suppressed=None)
        from .audio_guidance import GuidanceDecider
        self.display_decider = GuidanceDecider()
        if getattr(args, 'audio', False):
            from .audio_guidance import AudioGuidance, ClipPlayer
            self._audio_log('AUDIO_ENABLED', device=getattr(args, 'audio_device', None))
            player = ClipPlayer(device=getattr(args, 'audio_device', None), log=self._audio_log)
            self.audio = AudioGuidance(player, log=self._audio_log)
        self.log = self.log_path = None
        self.hand_pool = None
        self.hybrid_note = ''
        self.detector = None
        if getattr(args, 'detection', 'gemini') == 'hybrid':
            from .local_detector import LocalDetector
            self.detector = LocalDetector()
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
        self.stream_id = getattr(self, "stream_id", None) or uuid.uuid4().hex
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
        if self.detector is not None and self.detector.model is not None:
            self._identify_hybrid(job, raw, metadata, directory)
            return
        if self.detector is not None:
            self.hybrid_note = f'local detector {self.detector.status}; Gemini only for this request'
        prepared = None
        try:
            result = self._gemini_result(job, raw, metadata, directory)
            if self.factory and result['selection']['status'] == 'SELECTED':
                prepared = self.factory(job.image, result['selection']['target']['imageRegion'])
                replay_ok, replay_count = prepared.prime(self.history, job.received, job.epoch)
                (directory/'tracking-init.json').write_text(json.dumps({
                    'algorithm': 'OpenCV ViTTrack + SIFT recovery', 'replaySucceeded': replay_ok,
                    'replayedFrames': replay_count, 'historyBytesLimit': self.history.max_bytes,
                    'historySecondsLimit': self.history.seconds}), encoding='utf-8')
        except Exception as exc:
            result = self._failed(exc)
        self.responses.put((job, result, prepared))

    @staticmethod
    def _failed(exc):
        return {'provider': {'ok': False, 'error': {'kind': f'LOCAL_PROCESSING_ERROR_{type(exc).__name__}'}},
                'selection': {'status': 'PROVIDER_UNAVAILABLE'}, 'mode': '', 'timingMs': {}}

    def _gemini_result(self, job, raw, metadata, directory):
        client = self.client
        if getattr(self.args, 'target_kind', 'control') == 'object':
            import copy
            client = copy.copy(self.client)
            client.prompt = gemini.object_prompt(job.query)
        target_reply = (getattr(self.args, 'target_kind', 'control') == 'object'
                        and getattr(self.args, 'gemini_reply', 'inventory') == 'target')
        if target_reply:
            from . import target_query
        process_capture(raw, self.args.rotation, job.query, metadata, client, directory, fast=True,
                        runner=target_query.run if target_reply else None)
        result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
        if not target_reply and getattr(self.args, 'target_kind', 'control') == 'object' and result.get('screenModel'):
            from . import object_select
            selection = object_select.select(job.query, result['screenModel'])
            if selection.get('status') == 'AMBIGUOUS':
                selection['candidateLabels'] = object_select.candidates(selection, result['screenModel'])
            result['selection'] = selection
            (directory/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        return result

    def _identify_hybrid(self, job, raw, metadata, directory):
        """The usual Gemini search starts at once; the local detector runs alongside on the same frame.

        A clear local candidate locks immediately and Gemini's answer then confirms or contradicts it. Otherwise the
        Gemini answer is used as in GEMINI_ONLY, with its box replaced by an overlapping local box when one exists.
        One Gemini call per request, as in the baseline.
        """
        from . import hybrid
        answer = {}

        def ask():
            try:
                answer['result'] = self._gemini_result(job, raw, metadata, directory)
            except Exception as exc:
                answer['result'] = self._failed(exc)
        thread = threading.Thread(target=ask, daemon=True, name='r3-gemini')
        thread.start()
        candidates, local_ms = self.detector.detect(job.image, hybrid.phrases(job.query))
        chosen, reason = hybrid.local_choice(job.query, candidates)
        timing = {'local': round(local_ms, 1), 'provider': 0}
        local_dir = self.args.output / (job.frame_id + '-local')
        local_dir.mkdir(parents=True, exist_ok=True)
        if chosen is not None:
            result = hybrid.result(job, 'SELECTED', 'YOLO', reason, timing, candidates, chosen)
            (local_dir / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            self.hybrid_note = f'local lock in {local_ms:.0f} ms; waiting for Gemini check'
            self.responses.put((job, result, None))
        else:
            self.hybrid_note = f'local: {reason} ({local_ms:.0f} ms); waiting for Gemini'
            (local_dir / 'candidates.json').write_text(json.dumps(
                dict(reason=reason, localMs=local_ms, candidates=candidates), indent=2), encoding='utf-8')
        thread.join(timeout=self.args.timeout + 10)
        answer = answer.get('result') or self._failed(TimeoutError())
        if chosen is not None:
            verdict = hybrid.verify(answer, chosen, job.image.size)
            (local_dir / 'verification.json').write_text(json.dumps(verdict, indent=2), encoding='utf-8')
            self.hybrid_note = ''
            self.responses.put((job, verdict, None))
        else:
            self.hybrid_note = ''
            self.responses.put((job, hybrid.snap(answer, candidates, job.image.size, local_ms), None))

    def run(self):
        last_frame = None
        last_state = None
        last_log = 0.
        frames = []
        stale_handled = None
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
                    if self.audio is not None:
                        self.audio.reset()
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
                            corners = [tuple(map(float, p)) for p in match.polygon]
                            if self.session.state == 'HIDDEN':
                                for a, b in zip(corners, corners[1:] + corners[:1]):
                                    for k in range(0, 12, 2):
                                        draw.line([(a[0] + (b[0] - a[0]) * k / 12, a[1] + (b[1] - a[1]) * k / 12),
                                                   (a[0] + (b[0] - a[0]) * (k + 1) / 12,
                                                    a[1] + (b[1] - a[1]) * (k + 1) / 12)], fill=color, width=5)
                                caption = f'{self.session.label} HIDDEN - remembered location, not live'
                            else:
                                draw.line(corners + corners[:1], fill=color, width=5)
                                caption = f'{self.session.label} {self.session.state}'
                            x0, y0 = (float(v) for v in match.polygon.min(axis=0))
                            draw.text((x0, max(0., y0-14)), caption, fill=color)
                        self.alignment = None
                        self.offset = None
                        guide_tip = None
                        if hand_future is not None:
                            try:
                                hand = hand_future.result()
                                display = self.hand_bridge.draw(display, packet, hand, time.monotonic())
                                self.latest_hand = hand
                                self.hand_status = f'{hand.result.status} ({len(hand.result.hands)} hands)'
                                primary = hand.result.primary
                                if primary is not None and primary.index_tip_in_frame:
                                    guide_tip = tuple(map(float, primary.index_tip_px))
                                if polygon is not None and primary is not None and primary.index_tip_in_frame:
                                    self.alignment = fingertip_alignment(match.polygon, primary.index_tip_px, display.size)
                                    self.hand_status += ' | ' + draw_alignment(display, self.alignment, primary.index_tip_px)
                                    if self.spatial is not None and self.session.state != 'HIDDEN':
                                        try:
                                            rgbd = getattr(self.source, 'last_rgbd', None)
                                            rgbd = rgbd if rgbd is not None and rgbd.received == received else None
                                            aligned = self.spatial.aligned(rgbd, display.size, self.args.rotation)
                                            self.depth_frame = aligned
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
                        from .audio_guidance import Evidence
                        box = None
                        if polygon is not None:
                            (bx0, by0), (bx1, by1) = match.polygon.min(axis=0), match.polygon.max(axis=0)
                            box = (float(bx0), float(by0), float(bx1), float(by1))
                        measured = self.offset if self.offset is not None and self.offset.measured else None
                        evidence = Evidence(time.monotonic()-received, self.session.state == 'LOCKED', box, guide_tip,
                                            display.size, measured)
                        if self.audio is not None:
                            self.audio.update(evidence, time.monotonic())
                            direction = (self.audio.current, self.audio.mode)
                            self.hand_status += ' | ' + draw_line(display, self.audio.status(), 3,
                                (255, 255, 0) if self.audio.current else (170, 170, 170))
                        else:
                            direction = self.display_decider.decide(evidence)[:2]
                        depth_image = None
                        if getattr(self.args, 'depth_view', False) and self.depth_frame is not None:
                            from .spatial_panel import depth_view
                            depth_image = depth_view(self.depth_frame, box, guide_tip)
                        self.depth_frame = None
                        self.panel = dict(ticket=self.ticket, target=self.session.label or self.session.query,
                                          tracking=self.session.state, received=received, fps=self.fps,
                                          offset=self.offset, alignment=self.alignment, direction=direction,
                                          audio=dict(self.audio_state), depth_view=depth_image)
                        frames.append(time.monotonic())
                        while frames and frames[-1]-frames[0] > 2:
                            frames.pop(0)
                        self.fps = (len(frames)-1)/(frames[-1]-frames[0]) if len(frames) > 1 and frames[-1] > frames[0] else 0.
                        new_job = self.session.job(image, received, time.monotonic())
                        if new_job is not None:
                            threading.Thread(target=self.identify, args=(new_job, raw.copy(), dict(info)),
                                             daemon=True, name='r3-identify').start()
                        self.output = (self.ticket, display, received, self.session.state,
                                       self.session.describe(time.monotonic())+self._diagnostics()+' | Hands: '+self.hand_status
                                       + f' | {self.fps:.1f} FPS | frame age {(time.monotonic()-received)*1000:.0f} ms',
                                       self.session.mode, self.session.budget.total, packet.image)
                        self.process_ms = (time.monotonic()-started)*1000
                        self._log(received, match, display.size)
                        self.previous_received = received
                        last_frame = received
                    elif now-received > .3:
                        self.latest_frame = self.latest_target = self.latest_hand = None
                        grace = getattr(self.session, 'max_gap_s', .3)
                        if now-received > grace and stale_handled != received:
                            # Short stream pauses keep the tracker; the preview shows the last frame without a box.
                            stale_handled = received
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
            if self.audio is not None:
                self.audio.tick(time.monotonic())
            self.stopped.wait(.005 if getattr(self.args, 'tracker', 'orb') == 'lock' else .07)
        if self.hand_pool is not None:
            self.hand_pool.shutdown(wait=True)
        if self.hand_bridge is not None:
            self.hand_bridge.close()

    def _audio_log(self, event, **fields):
        if not getattr(self, "stream_id", None):
            self.stream_id = uuid.uuid4().hex
        record = dict(t=round(time.monotonic(), 3), event=event, **fields)
        state = getattr(self, 'audio_state', None)
        if state is not None:
            if event == 'PLAYBACK_STARTED':
                state.update(last_cue=fields.get('cue'), playback=f"playing {fields.get('cue')}")
            elif event in ('PLAYBACK_COMPLETED', 'PLAYBACK_INTERRUPTED'):
                state['playback'] = f"{event.split('_')[1].lower()} {fields.get('cue')}"
            elif event == 'PLAYBACK_FAILED':
                state['playback'] = f"FAILED: {fields.get('error')}"
            elif event == 'AUDIO_INITIALIZED':
                state['playback'] = f"ready on {fields.get('device')}"
            elif event == 'CUE_SUPPRESSED':
                state['suppressed'] = fields.get('reason')
            elif event == 'CUE_COMPUTED':
                state['suppressed'] = None
        print('AUDIO', json.dumps(record), flush=True)
        try:
            self.args.output.mkdir(parents=True, exist_ok=True)
            with (self.args.output / f'audio-{self.stream_id[:12]}.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(record) + '\n')
        except OSError:
            pass

    def _diagnostics(self):
        parts = []
        if self.detector is not None:
            parts.append('HYBRID detector ' + self.detector.status)
            if self.hybrid_note:
                parts.append(self.hybrid_note)
        else:
            parts.append('GEMINI_ONLY')
        if getattr(self.session, 'time_to_lock', None) is not None:
            parts.append(f'verified lock {self.session.time_to_lock:.1f}s after request')
        return ' | ' + ' | '.join(parts)

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
                      offset3d=None if self.offset is None else self.offset.record(),
                      detection=getattr(self.args, 'detection', 'gemini'), source=getattr(self.session, 'source', ''),
                      timeToLockS=getattr(self.session, 'time_to_lock', None),
                      audio=None if self.audio is None else dict(cue=self.audio.current, mode=self.audio.mode,
                                                                 reason=self.audio.reason))
        self.log.write(json.dumps(record)+'\n')
        self.logged += 1
        if self.logged % 15 == 0:
            self.log.flush()

    def close(self):
        self.stopped.set()
        self.source.close()
        self.thread.join(timeout=2)
        if self.audio is not None:
            self.audio.player.stop()
            self.audio.player.close()
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
    p.add_argument('--detection', choices=['gemini', 'hybrid'], default='gemini',
                   help='GEMINI_ONLY baseline (default) or HYBRID: local YOLO-World candidates, Gemini only when needed')
    p.add_argument('--target-kind', choices=['object', 'control'], default='object',
                   help='Gemini prompt for ordinary objects (default) or panel/touchscreen controls')
    p.add_argument('--gemini-reply', choices=['inventory', 'target'], default='inventory',
                   help='Object mode: full object list (default) or a short FOUND/NOT_FOUND/AMBIGUOUS answer for the request')
    p.add_argument('--media-resolution', choices=['default', 'low', 'medium', 'high'], default='medium',
                   help='Gemini 3 image detail: low 280, medium 560, default/high 1120 image tokens; lower is faster')
    p.add_argument('--thinking', choices=['minimal', 'low', 'medium', 'high'], default='low',
                   help='Gemini 3 thinking level for live identification; high measured 22-30 s per request')
    p.add_argument('--panel', action=argparse.BooleanOptionalAction, default=True,
                   help='Large spatial readout beside the video (default on)')
    p.add_argument('--depth-view', action=argparse.BooleanOptionalAction, default=False,
                   help='Small colour LiDAR depth map with the target box and fingertip in the panel')
    p.add_argument('--audio-device', help='Output device number or name for audio (see --audio-test); default device if omitted')
    p.add_argument('--audio', action=argparse.BooleanOptionalAction, default=False,
                   help='Spoken hand guidance from validated tracking (off by default; sighted tester, harmless object)')
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


def audio_device(value):
    return int(value) if value is not None and str(value).isdigit() else value


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if '--audio-test' in argv:
        from .audio_guidance import audio_test
        device = argv[argv.index('--audio-device') + 1] if '--audio-device' in argv else None
        return audio_test(audio_device(device))
    args = arguments(argv)
    args.audio_device = audio_device(args.audio_device)
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
        client.media_resolution = '' if args.media_resolution == 'default' else args.media_resolution
    if client.live and not client.configured:
        raise SystemExit('Set GEMINI_API_KEY and GEMINI_MODEL in environment or backend/.env; no request sent')
    import tkinter as tk
    root = tk.Tk()
    root.title('Reflex R3 live tracking')
    window_width = max(320, min(1700, root.winfo_screenwidth()-40))
    window_height = max(300, min(1000, root.winfo_screenheight()-80))
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
    panel = None
    if getattr(args, 'panel', True):
        from .spatial_panel import SpatialPanel
        panel = SpatialPanel(root)
        panel.frame.grid(row=3, column=1, sticky='ns', padx=(0, 8), pady=6)
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
            data = worker.panel
            if panel is not None and data and data.get('ticket') == ticket:
                panel.update(data)
            mode.set(f'{source_mode or ("SIMULATED" if args.replay else "REAL_API")} | {model_info} {effort} | image {getattr(client, 'media_resolution', '') or 'default'} | API requests: {requests}/{args.max_requests} | {"AUDIO ON" if getattr(args, "audio", False) else "audio off"}')
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
