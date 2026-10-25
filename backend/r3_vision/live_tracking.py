"""Windows live 2D target tracking. Frozen debug tool remains r3_vision.live_camera."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import queue
import re
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
        from .directional_distance import Calibration, DirectionalDistance
        self.distance_decider = DirectionalDistance(Calibration.load(getattr(args, 'distance_calibration', None)),
                                                    getattr(args, 'host', None), getattr(args, 'port', None))
        self.distance_readout = None
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
        self.hardware = None
        self.hardware_lock = threading.RLock()
        self.hardware_armed = False
        self.hardware_decider = GuidanceDecider()
        if getattr(args, 'arduino_ble', False):
            from .arduino_ble import ArduinoBLE
            self.hardware = ArduinoBLE(address=getattr(args, 'arduino_address', None))
            self.hardware.start()
        if getattr(args, 'audio', False):
            from .audio_guidance import AudioGuidance, ClipPlayer
            self._audio_log('AUDIO_ENABLED', device=getattr(args, 'audio_device', None))
            # Centimetre clips only when a physical calibration record allows validated 3D guidance.
            numeric = getattr(args, 'distance_calibration', None) is not None
            player = ClipPlayer(device=getattr(args, 'audio_device', None), log=self._audio_log, numeric=numeric)
            if getattr(args, 'agents', False):
                from .agent_audio import PriorityAudio
                player = PriorityAudio(player)
            # "Tracking lost" is spoken only when the target leaves the screen (see _watch_target_exit).
            self.audio = AudioGuidance(player, log=self._audio_log, numeric=numeric, lost_on_invalid=False)
        self.log = self.log_path = None
        self.hand_pool = None
        self.hybrid_note = ''
        self.detector = None
        if getattr(args, 'detection', 'gemini') in ('hybrid', 'allobjects', 'demo'):
            from .local_detector import LocalDetector
            self.detector = LocalDetector()
        self.spatial = None
        self.offset = None
        self.process_ms = None
        self.previous_received = None
        self.track_edge = getattr(args, 'track_edge', None) or (640 if getattr(args, 'tracker', 'orb') == 'lock' else 960)
        self.logged = 0
        self.commands = queue.Queue()
        self.scene_requests = queue.Queue(maxsize=1)
        self.scene_lock = threading.RLock()
        self.scene_generation = 0
        self.scene_inflight = False
        self.scene_note = ''
        self.responses = queue.Queue()
        self.stopped = threading.Event()
        self.output = None
        self.stream_id = getattr(self, "stream_id", None) or uuid.uuid4().hex
        from .all_objects import AllObjects, SelectionLatency
        self.latency = SelectionLatency(args.output / f'selection-{self.stream_id[:12]}.jsonl',
                                        getattr(args, 'detection', 'gemini'), log=lambda *a: print(*a, flush=True))
        self.process_ema = None
        self.all_objects = None
        if getattr(args, 'detection', 'gemini') == 'allobjects':
            self.all_objects = AllObjects(args, self.detector, client, self.session.budget, args.output,
                                          self.stream_id, health=lambda: self.process_ema, log=self._inventory_log)
        self.demo = None
        if getattr(args, 'detection', 'gemini') == 'demo':
            # Six registered objects; fixed vocabulary, CLIP and references warmed at launch; no background detection.
            from .demo_objects import STORE, DemoRecognizer
            self.demo = DemoRecognizer(self.detector, getattr(args, 'demo_objects', None) or STORE)
        self.latest_frame = None
        self.latest_target = None
        self.latest_hand = None
        self.hand_bridge = None
        self.hand_status = 'INITIALIZING' if getattr(args, 'hands', False) else 'OFF'
        self.ticket = 0
        self.agents = None
        if getattr(args, 'agents', False):
            from .agent_providers import AnthropicRouter, GeminiProvider
            from .orchestration_bridge import OrchestrationBridge
            self.agents = OrchestrationBridge(self, args.agent_router, args.agent_provider)
        self.thread = threading.Thread(target=self.run, daemon=True, name='r3-local-tracker')
        self.thread.start()

    def hardware_gate(self, armed=False):
        """Invalidate pending output synchronously, including while CV is blocked."""
        with self.hardware_lock:
            self.hardware_armed = armed
            self.hardware_decider.reset()
            if self.hardware is not None:
                self.hardware.stop()

    def hardware_update(self, evidence, received, hand_here):
        with self.hardware_lock:
            if self.hardware is None:
                return
            context = self.agents.context if self.agents is not None else None
            allowed = (self.hardware_armed and hand_here and self.session.state == 'LOCKED'
                       and not self.stopped.is_set()
                       and (self.agents is None or (context is not None and context.is_current())))
            # Local image-plane geometry only: never translate cloud prose, estimated depth,
            # forward/back, grasp or success into a servo movement.
            from .audio_guidance import Evidence
            cue = self.hardware_decider.decide(Evidence(
                time.monotonic() - received, allowed, evidence.box,
                evidence.tip, evidence.size, None))[0]
            self.hardware.submit(cue.lower() if cue else None, received)

    def identify(self, job, raw, info):
        directory = self.args.output / job.frame_id
        metadata = dict(info, frameID=job.frame_id, snapshotUTCms=int(time.time()*1000),
                        receivedMonotonic=job.received, snapshotMonotonic=time.monotonic(),
                        desktopROI=list(self.args.roi) if self.args.roi else None,
                        sourceKind='REC3D' if self.args.host else 'video')
        # Local (HYBRID/DEMO/ALL OBJECTS) results are completed by LockSession only; the older orb/vit
        # TrackingSession expects provider frame IDs, so those trackers keep the plain Gemini selection.
        local_ok = getattr(self.args, 'tracker', 'orb') == 'lock'
        if (local_ok and getattr(self, 'all_objects', None) is not None
                and self._identify_allobjects(job, raw, metadata, directory)):
            return
        if local_ok and getattr(self, 'demo', None) is not None and self._identify_demo(job):
            return
        if local_ok and self.detector is not None and self.detector.model is not None:
            self._identify_hybrid(job, raw, metadata, directory)
            return
        if self.detector is not None and not local_ok:
            self.hybrid_note = f'{self.args.tracker} tracker: local detection results need --tracker lock; Gemini only'
        elif self.detector is not None:
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
            if self.agents is not None:
                from .visual_agents import OBJECT_INSTRUCTIONS
                client.prompt += '\n' + OBJECT_INSTRUCTIONS
                client.specialist_instructions = OBJECT_INSTRUCTIONS
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

    def cancel_scene_speech(self):
        with self.scene_lock:
            self.scene_generation += 1
            self.scene_note = ''
            try:
                while True:
                    self.scene_requests.get_nowait()
            except queue.Empty:
                pass
            if self.audio is not None:
                self.audio.player.stop()

    def _watch_target_exit(self, match, size, margin=.04, delay_s=1.5, now=None):
        """Say "Tracking lost" only when the tracked object left the screen and stayed out for delay_s.

        Left the screen = the lock ended right after the object's box touched the image border, or the tracker
        reports that the remembered (covered) location moved out of the frame. Losing the hand, a hand covering
        the object, or the fingertip already being over the object never triggers it. If the object is tracked
        again within delay_s, nothing is said.
        """
        now = time.monotonic() if now is None else now
        state = self.session.state
        if state in ('LOCKED', 'UNCERTAIN', 'HIDDEN') and match.polygon is not None:
            (x0, y0), (x1, y1) = match.polygon.min(axis=0), match.polygon.max(axis=0)
            w, h = size
            self.target_at_edge = bool(x0 <= margin * w or y0 <= margin * h or x1 >= (1 - margin) * w
                                       or y1 >= (1 - margin) * h)
            self.target_tracked = True
            self.exit_since = None  # back on screen: cancel a pending announcement
            return False
        if state in ('TARGET LOST', 'REACQUIRING') and getattr(self, 'target_tracked', False):
            self.target_tracked = False
            if getattr(self, 'target_at_edge', False) or 'left the frame' in (getattr(match, 'reason', '') or ''):
                self.exit_since = now
        since = getattr(self, 'exit_since', None)
        if since is not None and now - since >= delay_s:
            self.exit_since = None
            print(f'TARGET LEFT THE SCREEN: {self.session.label}', flush=True)
            if self.audio is not None:
                self.audio.target_left_view(now)
            return True
        return False

    def describe_surroundings(self):
        """Coalesce presses while a description is queued or in flight."""
        with self.scene_lock:
            if self.stopped.is_set() or self.scene_inflight or not self.scene_requests.empty():
                return
            self.scene_requests.put((self.scene_generation, time.monotonic()))

    def _start_scene(self, request):
        generation, now = request
        with self.scene_lock:
            if self.stopped.is_set() or generation != self.scene_generation or self.scene_inflight:
                return
            budget = self.session.budget
            last = budget.last
            budget.last = None  # user press bypasses cooldown, but not request caps
            allowed, reason = budget.take(now)
            if not allowed:
                budget.last = last
                self.scene_note = f'DESCRIBE: not sent ({reason})'
                if self.audio is not None:
                    self.audio.player.play('DESCRIBE_FAILED')
                return
            self.scene_inflight = True
            threading.Thread(target=self._scene, args=(generation,), daemon=True, name='r3-scene').start()

    def _scene_current(self, generation):
        return not self.stopped.is_set() and generation == self.scene_generation

    def _scene(self, generation):
        from .scene_describe import describe
        try:
            item = self.source.snapshot()
            with self.scene_lock:
                if not self._scene_current(generation):
                    return
                if item is None or not 0 <= time.monotonic() - item[1] <= .3:
                    self.scene_note = 'DESCRIBE: no fresh camera frame'
                    if self.audio is not None:
                        self.audio.player.play('DESCRIBE_FAILED')
                    return
                raw, received, _ = item
                if self.audio is not None:
                    self.audio.player.play('LOOKING')
                self.scene_note = 'DESCRIBE: looking (Gemini)...'
            result = describe(upright(raw, self.args.rotation), self.client)
            age = time.monotonic() - received
            with self.scene_lock:
                if not self._scene_current(generation):
                    return
                if result['ok']:
                    self.scene_note = f"DESCRIBE ({result['ms'] / 1000:.1f}s; photo {age:.1f}s old): {result['summary']}"
                    if self.audio is not None:
                        self.audio.player.say(result['summary'])
                else:
                    self.scene_note = f"DESCRIBE failed: {result['error']}"
                    if self.audio is not None:
                        self.audio.player.play('DESCRIBE_FAILED')
                print(self.scene_note, flush=True)
            try:
                self.args.output.mkdir(parents=True, exist_ok=True)
                with (self.args.output / f'scene-{self.stream_id[:12]}.jsonl').open('a', encoding='utf-8') as out:
                    out.write(json.dumps(dict(result, photoAgeS=round(age, 2), t=round(time.monotonic(), 3))) + '\n')
            except OSError:
                pass
        finally:
            # Keep occupancy until the provider actually exits, including after Stop.
            with self.scene_lock:
                self.scene_inflight = False

    def describe(self):
        """Summarize demo objects without overlapping another description."""
        with self.scene_lock:
            if self.stopped.is_set() or self.scene_inflight or not self.scene_requests.empty():
                return
            generation = self.scene_generation
            self.scene_inflight = True

        def work():
            from .audio_guidance import SUMMARY_PHRASES
            from .demo_objects import OBJECTS
            try:
                item = self.source.snapshot()
                with self.scene_lock:
                    if not self._scene_current(generation):
                        return
                    if item is None or getattr(self, 'demo', None) is None or not 0 <= time.monotonic() - item[1] <= .3:
                        self.hybrid_note = 'DESCRIBE: needs DEMO mode and a fresh camera frame'
                        return
                raw, received, _ = item
                items, timing = self.demo.describe(upright(raw, self.args.rotation))
                age = time.monotonic() - received
                text = ('; '.join(f'{OBJECTS[k].name} {p}' for k, p in items) if items
                        else 'no demo object recognized with confidence')
                with self.scene_lock:
                    if not self._scene_current(generation):
                        return
                    self.hybrid_note = f'DESCRIBE ({timing.get("total", 0):.0f} ms, frame {age * 1000:.0f} ms old): {text}'
                    print(self.hybrid_note, flush=True)
                    if self.audio is not None:
                        cues = [f'SEE_{k}_{p}' for k, p in items] or ['SEE_NOTHING']
                        self.audio.player.play_parts([c for c in cues if c in SUMMARY_PHRASES])
            finally:
                with self.scene_lock:
                    self.scene_inflight = False
        threading.Thread(target=work, daemon=True, name='r3-describe').start()

    def _identify_demo(self, job):
        """DEMO: one confidently identified registered object locks locally (no Gemini call); a request naming
        several objects (\"bagel\") that matches several distinct ones is AMBIGUOUS. Anything else: HYBRID flow."""
        from . import hybrid
        from .demo_objects import OBJECTS
        try:
            choice = self.demo.choose(job)
        except Exception as exc:
            choice = dict(kind=None, reason=f'demo error {type(exc).__name__}', timing={}, candidates=[])
        ms = choice.get('timing', {}).get('total', 0.)
        self.latency.choice(f"DEMO {choice['kind'] or 'FALLBACK'}: {choice['reason']}")
        print(f"DEMO request '{job.query}': {choice['kind'] or 'Gemini fallback'} ({choice['reason']}) "
              f"{json.dumps({k: round(v) for k, v in choice.get('timing', {}).items()})} ms", flush=True)
        if choice['kind'] == 'LOCAL':
            w, h = job.image.size
            box = choice['box']
            chosen = dict(id=1, names=[choice['key']], score=1., box=(box[0] * w, box[1] * h, box[2] * w, box[3] * h))
            result = hybrid.result(job, 'SELECTED', 'DEMO LOCAL VERIFIED', choice['reason'],
                                   {'local': round(ms, 1), 'provider': 0}, [chosen], chosen,
                                   label=OBJECTS[choice['key']].name, provider_pending=False)
            self.hybrid_note = f"DEMO local match in {ms:.0f} ms; no Gemini call"
            self.responses.put((job, result, None))
            return True
        if choice['kind'] == 'AMBIGUOUS':
            result = hybrid.result(job, 'AMBIGUOUS', 'DEMO LOCAL', choice['reason'], {'local': round(ms, 1)}, [], provider_pending=False)
            result['selection']['candidateLabels'] = choice.get('labels', [])
            self.hybrid_note = f'DEMO: {choice["reason"]}'
            self.responses.put((job, result, None))
            return True
        self.hybrid_note = f"DEMO: {choice['reason']}; asking Gemini (HYBRID flow)"
        return False

    def _identify_allobjects(self, job, raw, metadata, directory):
        """Inventory first. True when answered; False sends the request down the HYBRID (or GEMINI_ONLY) flow.

        A VERIFIED inventory object locks with its box detected again on this request's frame, with no Gemini call.
        A unique plainly named generic object goes through the HYBRID flow with these current-frame candidates
        (local lock, then the usual Gemini check).
        """
        from . import hybrid
        try:
            choice = self.all_objects.choose(job)
        except Exception as exc:
            choice = dict(kind=None, reason=f'inventory error {type(exc).__name__}')
        self.latency.choice(f"{choice['kind'] or 'FALLBACK'}: {choice['reason']}")
        print(f"ALL OBJECTS request '{job.query}': {choice['kind'] or 'HYBRID fallback'} ({choice['reason']})",
              flush=True)
        if choice['kind'] == 'VERIFIED':
            track = choice['track']
            result = hybrid.result(job, 'SELECTED', f'INVENTORY #{track.id} VERIFIED', choice['reason'],
                                   {'local': round(choice['ms'], 1), 'provider': 0}, choice['candidates'],
                                   choice['candidate'], label=track.label, provider_pending=False)
            local_dir = self.args.output / (job.frame_id + '-inventory')
            local_dir.mkdir(parents=True, exist_ok=True)
            (local_dir / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            self.hybrid_note = f"inventory #{track.id} (verified earlier) in {choice['ms']:.0f} ms; no Gemini call"
            self.responses.put((job, result, None))
            return True
        if choice['kind'] == 'GENERIC' and self.detector is not None and self.detector.model is not None:
            self._identify_hybrid(job, raw, metadata, directory,
                                  local=(choice['candidates'], choice['ms'], f"INVENTORY #{choice['track'].id} LOCAL"))
            return True
        self.hybrid_note = f"inventory: {choice['reason']}; HYBRID request flow"
        return False

    def _identify_hybrid(self, job, raw, metadata, directory, local=None):
        """The usual Gemini search starts at once; the local detector runs alongside on the same frame.

        A clear local candidate locks immediately and Gemini's answer then confirms or contradicts it. Otherwise the
        Gemini answer is used as in GEMINI_ONLY, with its box replaced by an overlapping local box when one exists.
        One Gemini call per request, as in the baseline. `local` = (candidates, ms, source) already detected on
        job.image (ALL OBJECTS inventory) instead of a new detection.
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
        try:
            if local is None:
                candidates, local_ms = self.detector.detect(job.image, hybrid.phrases(job.query))
                source = 'YOLO'
            else:
                candidates, local_ms, source = local
        except Exception:
            # Detection failure does not cancel provider work already in progress.
            thread.join()
            self.responses.put((job, answer.get('result') or self._failed(TimeoutError()), None))
            return
        chosen, reason = hybrid.local_choice(job.query, candidates)
        timing = {'local': round(local_ms, 1), 'provider': 0}
        local_dir = self.args.output / (job.frame_id + '-local')
        local_dir.mkdir(parents=True, exist_ok=True)
        if chosen is not None:
            result = hybrid.result(job, 'SELECTED', source, reason, timing, candidates, chosen)
            (local_dir / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            self.hybrid_note = f'local lock in {local_ms:.0f} ms; waiting for Gemini check'
            self.responses.put((job, result, None))
        else:
            self.hybrid_note = f'local: {reason} ({local_ms:.0f} ms); waiting for Gemini'
            (local_dir / 'candidates.json').write_text(json.dumps(
                dict(reason=reason, localMs=local_ms, candidates=candidates), indent=2), encoding='utf-8')
        # The provider enforces its own timeout. Keep this background job occupied
        # until it actually exits; a join timeout must not permit overlapping calls.
        thread.join()
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
            if self.agents is not None:
                self.agents.drain()
            try:
                while True:
                    ticket, query = self.commands.get_nowait()
                    self.cancel_scene_speech()
                    self.ticket = ticket
                    self.session.find(query)
                    self.hardware_gate(bool(query))
                    self.latency.start(query, time.monotonic())
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
                    self._start_scene(self.scene_requests.get_nowait())
            except queue.Empty:
                pass
            try:
                while True:
                    job, result, prepared = self.responses.get_nowait()
                    if self.agents is not None:
                        result = self.agents.filter_result(job, result)
                        if result is None:
                            continue
                    if (result.get('mode') == 'HYBRID_VERIFY' and result.get('agrees') is True
                            and job.generation == self.session.generation):
                        self.latency.confirmed(time.monotonic())
                    if str(result.get('detectionSource', '')).startswith('DEMO LOCAL'):
                        # Answered on the laptop without Gemini: give the API request back and skip the cooldown,
                        # so automatic re-finding after a loss is never throttled by the cloud budget.
                        budget = self.session.budget
                        if budget.total > 0:
                            budget.total -= 1
                        if budget.times:
                            budget.times.pop()
                        budget.last = None
                    try:
                        self.session.complete(job, result, time.monotonic(), prepared_tracker=prepared)
                        if self.agents is not None and (self.agents.context is None or
                                                        not self.agents.context.is_current()):
                            self.session.stop()
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
                        if self.agents is not None:
                            self.agents.observe(packet)
                        display = packet.image.copy()
                        hand_future = (self.hand_pool.submit(self.hand_bridge.process, packet)
                                       if self.hand_bridge is not None else None)
                        if self.all_objects is not None:
                            self.all_objects.offer(packet, raw, info)
                            if getattr(self.args, 'inventory_overlay', True):
                                # Thin debug boxes under the lock box; never used for guidance.
                                self.all_objects.draw(display, time.monotonic())
                        match = self.session.update(packet.image, received, now)
                        self.latency.update(self.session)
                        self._watch_target_exit(match, packet.image.size)
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
                        self.distance_readout = self.distance_decider.update(
                            self.offset, packet, self.depth_frame, self.session.state == 'LOCKED',
                            self.latest_hand is not None and self.latest_hand.stamp == packet.stamp
                            and guide_tip is not None, time.monotonic())
                        physical_valid = bool(self.distance_readout.axes)
                        evidence = Evidence(time.monotonic()-received, physical_valid, box, guide_tip,
                                            display.size, measured)
                        hand_here = (self.latest_hand is not None and self.latest_hand.stamp == packet.stamp
                                     and guide_tip is not None)
                        if self.audio is not None:
                            self.audio_2d_until = None
                            if physical_valid:
                                self.audio.update(evidence, time.monotonic())
                            elif (self.agents is None and getattr(self.args, 'detection', '') == 'demo'
                                  and self.session.state == 'LOCKED'
                                  and box is not None and hand_here and time.monotonic() - received <= .3):
                                # DEMO without validated 3D: direction words from this frame's pixels, plus word-only
                                # Forward/Back and Grab from this frame's ESTIMATED depth (labelled 2D+EST, never cm).
                                same = (self.offset if self.offset is not None
                                        and getattr(self.offset, 'received_monotonic', None) == received else None)
                                self.audio.update(Evidence(time.monotonic()-received, True, box, guide_tip,
                                                           display.size, same), time.monotonic())
                                self.audio_2d_until = received + .3
                            else:
                                # Preserve the existing player; unreliable physical evidence stops it immediately.
                                self.audio.reset()
                                self.audio.reason = self.distance_readout.reason
                            direction = (self.audio.current, self.audio.mode)
                            self.hand_status += ' | ' + draw_line(display, self.audio.status(), 3,
                                (255, 255, 0) if self.audio.current else (170, 170, 170))
                        else:
                            direction = self.display_decider.decide(evidence)[:2]
                        self.hardware_update(evidence, received, hand_here)
                        depth_image = None
                        if getattr(self.args, 'depth_view', False) and self.depth_frame is not None:
                            from .spatial_panel import depth_view
                            depth_image = depth_view(self.depth_frame, box, guide_tip)
                        self.depth_frame = None
                        self.panel = dict(ticket=self.ticket, target=self.session.label or self.session.query,
                                          tracking=self.session.state, received=received, fps=self.fps,
                                          offset=self.offset, alignment=self.alignment, direction=direction,
                                          distances=self.distance_readout, audio=dict(self.audio_state), depth_view=depth_image)
                        frames.append(time.monotonic())
                        while frames and frames[-1]-frames[0] > 2:
                            frames.pop(0)
                        self.fps = (len(frames)-1)/(frames[-1]-frames[0]) if len(frames) > 1 and frames[-1] > frames[0] else 0.
                        new_job = self.session.job(image, received, time.monotonic())
                        if new_job is not None:
                            threading.Thread(target=self.identify, args=(new_job, raw.copy(), dict(info)),
                                             daemon=True, name='r3-identify').start()
                        if self.all_objects is not None:
                            # After the request job, so the user's Find always has priority on the budget.
                            self.all_objects.maybe_verify(time.monotonic(), self.session)
                        self.output = (self.ticket, display, received, self.session.state,
                                       self.session.describe(time.monotonic())+self._diagnostics()+' | Hands: '+self.hand_status
                                       + f' | {self.fps:.1f} FPS | frame age {(time.monotonic()-received)*1000:.0f} ms',
                                       self.session.mode, self.session.budget.total, packet.image)
                        self.process_ms = (time.monotonic()-started)*1000
                        self.process_ema = (self.process_ms if self.process_ema is None
                                            else .8*self.process_ema + .2*self.process_ms)
                        self._log(received, match, display.size)
                        self.previous_received = received
                        last_frame = received
                    elif now-received > .3:
                        if self.hardware is not None:
                            self.hardware.stop()
                        self.latest_frame = self.latest_target = self.latest_hand = None
                        grace = getattr(self.session, 'max_gap_s', .3)
                        if now-received > grace and stale_handled != received:
                            # Short stream pauses keep the tracker; the preview shows the last frame without a box.
                            stale_handled = received
                            self.session.update(working_image(raw), received, now)
                            self.output = (self.ticket, None, received, 'TARGET LOST', 'Camera frame stale; box hidden',
                                           self.session.mode, self.session.budget.total)
                else:
                    if self.hardware is not None:
                        self.hardware.stop()
                    self.latest_frame = self.latest_target = self.latest_hand = None
                    self.session.update(None, now-1, now)
                    self.output = (self.ticket, None, now, 'TARGET LOST', self.source.status,
                                   self.session.mode, self.session.budget.total)
                state = (self.session.state, self.session.detail)
                if self.agents is not None:
                    self.agents.tracking(time.monotonic())
                if state != last_state and (last_state is None or state[0] != last_state[0]
                                             or time.monotonic()-last_log >= 1):
                    # State/detail are local or sanitized pipeline categories, never endpoint/key values.
                    print(f'{self.session.state}: {self.session.detail}', flush=True)
                    last_state = state
                    last_log = time.monotonic()
            except Exception as exc:
                if self.hardware is not None:
                    self.hardware.stop()
                self.latest_frame = self.latest_target = self.latest_hand = None
                self.session.tracker = None
                self.session.hits = 0
                self.session.state, self.session.detail = 'ERROR', f'Local tracker failure ({type(exc).__name__}); box hidden'
                self.output = (self.ticket, None, time.monotonic(), 'ERROR', self.session.detail,
                               self.session.mode, self.session.budget.total)
            if self.audio is not None:
                audio_now = time.monotonic()
                stamp = self.latest_frame.stamp if self.latest_frame is not None else None
                deadline = (getattr(self.distance_readout, 'valid_until_monotonic', None)
                            or getattr(self, 'audio_2d_until', None))
                if (stamp is None or not 0 <= audio_now - stamp.received_monotonic_s <= .3
                        or deadline is None or audio_now > deadline):
                    self.audio.reset()
                    self.audio.reason = 'no fresh validated frame (300 ms limit)'
                else:
                    self.audio.tick(audio_now)
            self.stopped.wait(.005 if getattr(self.args, 'tracker', 'orb') == 'lock' else .07)
        if self.all_objects is not None:
            self.all_objects.close()
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

    def _inventory_log(self, event, **fields):
        record = dict(t=round(time.monotonic(), 3), event=event, **fields)
        print('INVENTORY', json.dumps(record), flush=True)
        try:
            self.args.output.mkdir(parents=True, exist_ok=True)
            with (self.args.output / f'inventory-{self.stream_id[:12]}.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(record) + '\n')
        except OSError:
            pass

    def _diagnostics(self):
        parts = []
        if getattr(self, 'hardware', None) is not None:
            parts.append('Arduino BLE: ' + self.hardware.status)
        if getattr(self, 'all_objects', None) is not None:
            parts.append(self.all_objects.summary())
            if self.hybrid_note:
                parts.append(self.hybrid_note)
        elif getattr(self, 'demo', None) is not None:
            parts.append('DEMO (6 objects) ' + self.demo.status + ' | YOLO ' + getattr(self.detector, 'status', 'off'))
            if self.hybrid_note:
                parts.append(self.hybrid_note)
        elif self.detector is not None:
            parts.append('HYBRID detector ' + self.detector.status)
            if self.hybrid_note:
                parts.append(self.hybrid_note)
        else:
            parts.append('GEMINI_ONLY')
        if getattr(self.session, 'time_to_lock', None) is not None:
            parts.append(f'verified lock {self.session.time_to_lock:.1f}s after request')
        if getattr(self, 'scene_note', ''):
            parts.append(self.scene_note)
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
                      directionalDistance=None if self.distance_readout is None else dict(
                          status=self.distance_readout.status, reason=self.distance_readout.reason,
                          cameraRelative=True, estimated=self.distance_readout.estimated,
                          axes=[dict(axis=a.axis, direction=a.direction, cm=round(a.cm, 1))
                                for a in self.distance_readout.axes]),
                      detection=getattr(self.args, 'detection', 'gemini'), source=getattr(self.session, 'source', ''),
                      timeToLockS=getattr(self.session, 'time_to_lock', None),
                      allObjects=None if getattr(self, 'all_objects', None) is None else self.all_objects.record(),
                      audio=None if self.audio is None else dict(cue=self.audio.current, mode=self.audio.mode,
                                                                 reason=self.audio.reason))
        self.log.write(json.dumps(record)+'\n')
        self.logged += 1
        if self.logged % 15 == 0:
            self.log.flush()

    def close(self):
        if getattr(self, 'hardware', None) is not None:
            self.hardware_gate()
            self.hardware.close()
        if getattr(self, 'agents', None) is not None:
            self.agents.close()
        self.stopped.set()
        self.cancel_scene_speech()
        self.source.close()
        self.thread.join(timeout=2)
        if self.audio is not None:
            self.audio.player.stop()
            self.audio.player.close()
        if self.log is not None:
            self.log.close()


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--arduino-ble', action='store_true', help='Opt-in Arduino 101 BLE image-plane servo cues')
    p.add_argument('--arduino-address', help='Explicit BLE device address (macOS device UUID)')
    p.add_argument('--agents', action='store_true', help='Direct Anthropic/Gemini multi-agent orchestration')
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--host', help='Current user-supplied PathOn REC3D host')
    src.add_argument('--video', help='Explicit OpenCV camera index, URL, or test video')
    p.add_argument('--port', type=int)
    p.add_argument('--rotation', type=int, choices=[0, 90, 180, 270], default=0)
    p.add_argument('--timeout', type=float, default=60)
    p.add_argument('--tracker', choices=['lock', 'vit', 'orb'], default='lock',
                   help='Object lock: ViTTrack + object-only SIFT identity (default); previous ViTTrack hybrid; old ORB debugger')
    p.add_argument('--demo-objects', type=Path,
                   help='DEMO: reference photo folder (default backend/.runtime/demo-objects)')
    p.add_argument('--detection', choices=['gemini', 'hybrid', 'allobjects', 'demo'], default='hybrid',
                   help='HYBRID (default): request-specific YOLO-World and Gemini verification; GEMINI_ONLY baseline; '
                        'ALLOBJECTS: opt-in experimental background inventory and verification, HYBRID fallback')
    p.add_argument('--discovery-source', choices=['world', 'yoloe'], default='world',
                   help='ALL OBJECTS discovery: YOLO-World general vocabulary (default) or YOLOE-26n prompt-free '
                        '(labels are hints only until Gemini verifies)')
    p.add_argument('--discovery-interval', type=float, default=1.,
                   help='ALL OBJECTS: minimum seconds between discovery passes (raised automatically when busy)')
    p.add_argument('--discovery-conf', type=float, default=.25, help='ALL OBJECTS: minimum detector confidence shown')
    p.add_argument('--discovery-threads', type=int, default=4,
                   help='ALL OBJECTS: PyTorch CPU threads (0 = PyTorch default); leaves cores for camera and hands')
    p.add_argument('--discovery-busy-ms', type=float, default=120,
                   help='ALL OBJECTS: skip discovery while the tracking loop averages more than this per frame')
    p.add_argument('--verify-interval', type=float, default=15,
                   help='ALL OBJECTS: minimum seconds between background Gemini inventory checks')
    p.add_argument('--verify-reserve', type=int, default=5,
                   help='ALL OBJECTS: API requests of --max-requests never used by background checks')
    p.add_argument('--inventory-overlay', action=argparse.BooleanOptionalAction, default=True,
                   help='ALL OBJECTS: draw every inventory object with ID, label, confidence and status')
    p.add_argument('--target-kind', choices=['object', 'control'], default='object',
                   help='Gemini prompt for ordinary objects (default) or panel/touchscreen controls')
    p.add_argument('--gemini-reply', choices=['inventory', 'target'], default='inventory',
                   help='Object mode: full object list (default) or a short FOUND/NOT_FOUND/AMBIGUOUS answer for the request')
    p.add_argument('--media-resolution', choices=['default', 'low', 'medium', 'high'], default='medium',
                   help='Gemini 3 image detail: low 280, medium 560, default/high 1120 image tokens; lower is faster')
    p.add_argument('--thinking', choices=['minimal', 'low', 'medium', 'high'], default='low',
                   help='Gemini 3 thinking level for live identification; high measured 22-30 s per request')
    p.add_argument('--web-dashboard', action='store_true', help='Serve the local browser display at 127.0.0.1')
    p.add_argument('--web-port', type=int, default=8765, help='Loopback dashboard port (default 8765)')
    p.add_argument('--panel', action=argparse.BooleanOptionalAction, default=True,
                   help='Large spatial readout beside the video (default on)')
    p.add_argument('--distance-calibration', type=Path,
                   help='Physical camera/depth axis validation record; absent means no physical distance instructions')
    p.add_argument('--depth-view', action=argparse.BooleanOptionalAction, default=False,
                   help='Small colour LiDAR depth map with the target box and fingertip in the panel')
    p.add_argument('--audio-device', help='Output device number or name for audio (see --audio-test); default device if omitted')
    p.add_argument('--voice', action=argparse.BooleanOptionalAction, default=False,
                   help='Always-listening voice commands (offline Windows speech): "find the Pepsi can", '
                        '"what\'s around me", "stop" / "be quiet", "stop tracking"')
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
    if not 1 <= args.web_port <= 65535:
        p.error('--web-port must be 1..65535')
    args.roi = None  # Existing Source interface; ROI remains in the frozen debugging tool.
    if args.reidentify_after is None:
        # DEMO re-finds a lost object locally (no Gemini call when recognized), so it can retry quickly.
        args.reidentify_after = 1.5 if args.detection == 'demo' else 5. if args.tracker == 'lock' else 2.
    if args.host and (args.port is None or not 1 <= args.port <= 65535):
        p.error('--host requires current REC3D --port (1..65535)')
    for name in ['timeout', 'cooldown', 'reidentify_after', 'reference_ttl', 'discovery_interval', 'verify_interval']:
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
    if args.agents and (args.tracker != 'lock' or args.target_kind != 'object' or args.replay
                        or args.detection not in ('gemini', 'hybrid')):
        raise SystemExit('--agents requires --tracker lock --target-kind object --detection hybrid (or gemini); '
                         'use injected providers for automated simulation')
    args.audio_device = audio_device(args.audio_device)
    if args.agents:
        from .agent_providers import AnthropicRouter, GeminiProvider
        gemini.load_dotenv(Path(__file__).resolve().parents[1] / '.env')
        try:
            args.agent_router = AnthropicRouter.from_env()
            args.agent_provider = GeminiProvider.from_env()
            args.agent_router.verify_models()
            args.agent_provider.verify_models()
            # Object verification uses the existing client and its configured model.
            import os
            GeminiProvider(os.getenv('GEMINI_API_KEY'), os.getenv('GEMINI_MODEL')).verify_models()
        except (ValueError, RuntimeError, TimeoutError) as exc:
            raise SystemExit(f'Agent configuration unavailable: {exc}') from None
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
    client.timeout_s = min(args.timeout, 5.) if args.agents else args.timeout
    if args.target_kind == 'object':
        from .gemini import OBJECT_PROMPT
        client.prompt = OBJECT_PROMPT
    if client.live and client.model.startswith('gemini-3'):
        client.thinking_level = args.thinking
        client.media_resolution = '' if args.media_resolution == 'default' else args.media_resolution
    if client.live and not client.configured:
        if args.detection != 'demo':
            raise SystemExit('Set GEMINI_API_KEY and GEMINI_MODEL in environment or backend/.env; no request sent')
        # DEMO recognizes, tracks and guides locally; only the Gemini fallback and Describe need the key.
        print('DEMO OFFLINE: no Gemini key; local recognition only (fallback and Describe report MISSING_API_KEY)',
              flush=True)
    import tkinter as tk
    root = tk.Tk()
    root.title('VisionOS R3 live tracking')
    # Clean white theme for every default widget (the spatial panel sets its own matching colours).
    root.configure(bg='white')
    for pattern, value in (('*Background', 'white'), ('*Foreground', '#111827'), ('*Font', 'Segoe 10'),
                           ('*Button.Relief', 'groove'), ('*Button.activeBackground', '#f3f4f6'),
                           ('*Entry.Relief', 'solid'), ('*Entry.highlightThickness', 1),
                           ('*Entry.highlightColor', '#9ca3af'), ('*Entry.highlightBackground', '#d1d5db')):
        root.option_add(pattern, value)
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
    status = tk.StringVar(value='Say or type what to find, then Find & Track')
    # One fixed line (no wrapping), so the video area never jumps in size.
    status_label = tk.Label(root, textvariable=status, anchor='w', font=('Segoe UI', 13, 'bold'))
    status_label.grid(row=1, column=0, sticky='ew', padx=10)
    mode = tk.StringVar(value='SIMULATED' if args.replay else 'REAL_API on demand; no movement outputs')
    mode_label = tk.Label(root, textvariable=mode, anchor='w', fg='#6b7280')
    mode_label.grid(row=2, column=0, sticky='ew', padx=10)
    details = tk.BooleanVar(value=False)

    def toggle_details():
        from . import alignment
        alignment.SHOW_TEXT = details.get()  # technical text drawn on the video only with Details
        (mode_label.grid if details.get() else mode_label.grid_remove)()
        if panel is not None:
            panel.set_details(details.get())
    tk.Checkbutton(toolbar, text='Details', variable=details, command=lambda: toggle_details()).grid(
        row=0, column=5, padx=(8, 0))
    viewport = tk.Frame(root)
    viewport.grid(row=3, column=0, sticky='nsew', padx=8, pady=6)
    heard = tk.StringVar(value='')
    if getattr(args, 'voice', False):
        # Everything the microphone hears, newest first; commands show what they triggered.
        heard.set('YOU SAID: (listening...)')
        tk.Label(root, textvariable=heard, anchor='w', justify='left', font=('Segoe UI', 14, 'bold'),
                 fg='#1d4ed8', wraplength=window_width - 560).grid(row=4, column=0, sticky='ew', padx=12, pady=(0, 8))
    # Image dimensions must never enlarge the window or push controls off-screen.
    viewport.grid_propagate(False)
    viewport.pack_propagate(False)
    preview = tk.Label(viewport, text='LIVE VIDEO - waiting for current frame', compound='top')
    preview.pack(expand=True, fill='both')
    panel = None
    if getattr(args, 'panel', True):
        from .spatial_panel import SpatialPanel
        panel = SpatialPanel(root)
        # Same top as the status lines and same bottom as the video, so both columns line up.
        panel.frame.grid(row=1, column=1, rowspan=3, sticky='nsew', padx=(0, 8), pady=(0, 6))
    worker = TrackingWorker(args, client)
    agent_answer = None
    if args.agents:
        answer_frame = tk.LabelFrame(root, text='VisionOS response')
        answer_frame.grid(row=5, column=0, columnspan=2, sticky='ew', padx=10, pady=6)
        agent_answer = tk.Text(answer_frame, height=4, wrap='word', state='disabled', takefocus=True)
        answer_scroll = tk.Scrollbar(answer_frame, command=agent_answer.yview)
        agent_answer.configure(yscrollcommand=answer_scroll.set)
        answer_scroll.pack(side='right', fill='y')
        agent_answer.pack(side='left', fill='both', expand=True)
    toggle_details()
    ticket = 0
    last = None
    last_size = None
    last_clean = None  # newest frame without overlays, shown through short gaps (never with a box)
    last_good = 0.

    def short_status(state, detail):
        """Plain one-line status; the full technical text is shown with Details."""
        target = worker.session.label or worker.session.query or query.get()
        text = {'LOCKED': f'LOCKED: {target}', 'UNCERTAIN': f'Checking: {target}',
                'HIDDEN': f'{target} is covered; remembering where it was',
                'REACQUIRING': f'Re-finding {target}…', 'IDENTIFYING': f'Looking for {target}…',
                'SEARCHING': f'Searching for: {target}' if worker.session.active else 'Say or type what to find',
                'TARGET LOST': f'Lost {target}; looking again' if worker.session.active else 'Not tracking',
                'AMBIGUOUS': detail.split(' | ')[0], 'ERROR': 'Something went wrong; try Find & Track again'}
        return text.get(state, state)

    def find():
        nonlocal ticket, last
        worker.hardware_gate()
        if worker.agents is not None:
            try:
                worker.agents.coordinator.submit(query.get())
            except ValueError:
                status.set('Enter a request containing 1–1000 characters.')
                return
            if dashboard is not None:
                dashboard.invalidate()
            last = None
            return
        if re.search(r"\b(describe|what'?s around|what is around|around me|surroundings)\b", query.get().lower()):
            worker.describe_surroundings()
            if dashboard is not None:
                dashboard.allow_current_ticket()
            return
        ticket += 1
        if dashboard is not None:
            dashboard.invalidate()
        last = None
        status.set(f'Looking for {query.get()}…')
        worker.cancel_scene_speech()
        worker.commands.put((ticket, query.get()))

    tk.Button(toolbar, text='Find & Track', command=find).grid(row=0, column=1, padx=(0, 8))

    def stop():
        nonlocal ticket, last
        worker.hardware_gate()
        if worker.agents is not None:
            worker.agents.coordinator.stop()
            if dashboard is not None:
                dashboard.invalidate()
            last = None
            return
        ticket += 1
        if dashboard is not None:
            dashboard.invalidate()
        last = None
        worker.cancel_scene_speech()
        worker.commands.put((ticket, ''))
        status.set('Stopped')

    tk.Button(toolbar, text='Stop tracking', command=stop).grid(row=0, column=2)
    def describe():
        if worker.agents is not None:
            worker.agents.coordinator.submit('Describe surroundings')
        else:
            worker.describe_surroundings()

    tk.Button(toolbar, text='Describe surroundings', command=describe).grid(
        row=0, column=3, padx=(8, 0))
    if args.detection == 'demo':
        tk.Button(toolbar, text='Quick: 6 objects', command=worker.describe).grid(row=0, column=4, padx=(8, 0))
    root.bind('<Return>', lambda event: find())
    voice = None
    if getattr(args, 'voice', False):
        from .voice_commands import VoiceCommands
        player = worker.audio.player if worker.audio is not None else None
        voice = VoiceCommands(Path(__file__).resolve().parents[1] / '.runtime/voice',
                              open_ended=args.agents,
                              speaking=(lambda: player.speaking()) if player is not None else (lambda: False),
                              log=lambda event, **fields: print('VOICE', event, json.dumps(fields), flush=True))

    def voice_command(command, argument):
        from .demo_objects import OBJECTS
        player = worker.audio.player if worker.audio is not None else None
        if command == 'wake':
            root.bell()  # Local acknowledgement; never starts or cancels tracking.
            return
        if worker.agents is not None:
            if command in ('quiet', 'stop_tracking'):
                stop()
            elif command == 'request':
                query.set(argument)
                find()
            elif command == 'find':
                query.set(f'Find the {OBJECTS[argument].name}')
                find()
            elif command == 'describe':
                describe()
            return
        if command == 'quiet':
            worker.hardware_gate()
            worker.cancel_scene_speech()
            if player is not None:
                player.stop()
                player.muted = True  # silent until the next command
            return
        if player is not None:
            player.muted = False
        if command == 'find':
            query.set(f'Find the {OBJECTS[argument].name}')
            if player is not None:
                player.play(f'FINDING_{argument}')
            find()
        elif command == 'describe':
            worker.describe_surroundings()
        elif command == 'stop_tracking':
            stop()

    dashboard = None
    last_publish = 0.
    if args.web_dashboard:
        from .dashboard import Dashboard
        try:
            dashboard = Dashboard(args.web_port)
            dashboard.start()
            print(f'VisionOS browser dashboard: {dashboard.url}', flush=True)
        except OSError as error:
            print(f'Browser dashboard unavailable: {error}. Desktop tracking remains available.', flush=True)

    def tick():
        nonlocal last, last_size, last_publish, ticket, last_clean, last_good
        if worker.agents is not None:
            ticket = worker.ticket
        if dashboard is not None:
            while not dashboard.commands.empty():
                command = dashboard.commands.get_nowait()
                action = command['action']
                if action in ('find', 'request'):
                    query.set(command['query'])
                    find()
                elif action == 'stop':
                    stop()
                elif action == 'describe':
                    describe()
                elif action == 'recall' and worker.agents is not None:
                    worker.agents.coordinator.submit('What did I miss?')
                elif action == 'quick' and args.detection == 'demo':
                    worker.describe()
        if voice is not None:
            voice.tick(time.monotonic())
        while voice is not None and not voice.commands.empty():
            command, argument, _ = voice.commands.get_nowait()
            voice_command(command, argument)
        if voice is not None and voice.transcript:
            names = {'wake': 'LISTENING', 'find': 'FIND', 'describe': 'DESCRIBE', 'quiet': 'QUIET', 'stop_tracking': 'STOP TRACKING',
                     'request': 'REQUEST'}
            lines = [f'"{item["text"]}"' + (f'  →  {names[item["command"]]}' if item['command'] else '')
                     for item in list(voice.transcript)[::-1][:3]]
            heard.set('YOU SAID: ' + lines[0] + ('\nearlier: ' + '   |   '.join(lines[1:]) if lines[1:] else ''))
        item = worker.output
        if item and item[0] == ticket:
            _, image, received, state, detail, source_mode, requests = item[:7]
            clean = item[7] if len(item) > 7 else None
            now = time.monotonic()
            age = now-received
            fresh = 0 <= age <= .3
            delayed = not fresh and clean is not None and 0 <= age <= 1.
            size = (max(1, viewport.winfo_width()-12), max(1, viewport.winfo_height()-12))
            show_details = details.get()
            message = f'{state}: {detail}' if show_details else short_status(state, detail)
            if fresh and clean is not None:
                last_clean, last_good = clean, now
            if image is None or not (fresh or delayed):
                if last_clean is not None and now - last_good <= 1.5:
                    # Short gap: keep the last picture WITHOUT the box (stale data is never shown as live)
                    # instead of blanking the video, which flickers.
                    if last != 'held' or size != last_size:
                        fitted = last_clean.copy()
                        fitted.thumbnail(size)
                        photo = ImageTk.PhotoImage(fitted)
                        preview.configure(image=photo, text='Camera catching up…' if show_details else '')
                        preview.image = photo
                        last, last_size = 'held', size
                else:
                    preview.configure(image='', text='No video from the camera')
                    preview.image = None
                    last = None
                status.set(message if image is None else short_status('TARGET LOST', detail) if not show_details
                           else f'{state}: frame {age*1000:.0f} ms old; box hidden')
            elif (item, fresh) != last or size != last_size:
                fitted = (image if fresh else clean).copy()
                fitted.thumbnail(size)
                photo = ImageTk.PhotoImage(fitted)
                caption = ('' if not show_details else f'LIVE VIDEO | {state} | 2D VISUAL TEST ONLY' if fresh else
                           f'LIVE VIDEO DELAYED {age*1000:.0f} ms | box hidden until a fresh frame')
                preview.configure(image=photo, text=caption)
                preview.image = photo
                status.set(message)
                last = (item, fresh)
                last_size = size
            model_info = getattr(client, 'model', '')
            effort = getattr(client, 'thinking_level', '')
            data = worker.panel
            if panel is not None and data and data.get('ticket') == ticket:
                panel.update(data)
            mode.set(f'{source_mode or ("SIMULATED" if args.replay else "REAL_API")} | {model_info} {effort} | image {getattr(client, 'media_resolution', '') or 'default'} | API requests: {requests}/{args.max_requests} | {"AUDIO ON" if getattr(args, "audio", False) else "audio off"}'
                     + (f' | VOICE: {voice.status}' + (f' | heard {voice.last_heard}' if voice.last_heard else '')
                        if voice is not None else ''))
        if dashboard is not None and time.monotonic() - last_publish >= .1:
            last_publish = time.monotonic()
            dashboard.publish(worker.output, worker.panel, ticket,
                              mode='SIMULATED' if args.replay else 'LIVE',
                              quick=args.detection == 'demo', voice=voice,
                              orchestration=worker.agents.coordinator.snapshot() if worker.agents else None)
        if worker.agents is not None:
            agent_state = worker.agents.coordinator.snapshot()
            response = agent_state.get('response') or ''
            if agent_answer.get('1.0', 'end-1c') != response:
                agent_answer.configure(state='normal')
                agent_answer.delete('1.0', 'end')
                agent_answer.insert('1.0', response)
                agent_answer.configure(state='disabled')
            status.set(f"{agent_state['agent']}: {agent_state['status']} | "
                       f"{agent_state.get('clarification') or agent_state.get('error') or agent_state['goal']}")
        root.after(30, tick)

    def close():
        if voice is not None:
            voice.close()
        root.destroy()
        worker.close()
        if dashboard is not None:
            dashboard.close()
    root.protocol('WM_DELETE_WINDOW', close)
    tick()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
