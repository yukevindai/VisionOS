"""Adapter to the existing single-camera/local-tracker demo; no movement policy."""
from __future__ import annotations

import copy
import queue
import threading
import time

from .orchestration import AgentResult, Coordinator
from .visual_agents import SharedFrameService, VisualAgents, validate_object_result


class OrchestrationBridge:
    def __init__(self, worker, router, provider):
        self.worker = worker
        self.frames = SharedFrameService()
        self.pending = queue.Queue(maxsize=1)
        self.lock = threading.RLock()
        self.context = None
        self.provisional = None
        self.last_state = None
        self.had_lock = False
        self.visual = VisualAgents(self.frames, provider, self._find)
        self.coordinator = Coordinator(router, self.visual.find, self.visual.describe,
                                       self.visual.recall, self.stop_local, self.speak)

    def _replace(self, value):
        with self.lock:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                pass
            self.pending.put_nowait(value)

    def _find(self, query, context, frame):
        with self.coordinator.lock:
            context.check()
            self._replace((query, context))
        return None  # Explicit asynchronous handoff to existing identification worker.

    def stop_local(self):
        # Audio is gated synchronously, even when camera/CV work is blocked.
        if self.worker.audio is not None:
            self.worker.audio.player.enable_guidance(False)
            self.worker.audio.player.interrupt_all()
        self._replace(('', None))

    def speak(self, text, context):
        if context.is_current() and self.worker.audio is not None:
            self.worker.audio.player.say(text, context=context)

    def drain(self):
        """Called only by the existing local tracking thread."""
        try:
            query, context = self.pending.get_nowait()
        except queue.Empty:
            return
        if context is not None and not context.is_current():
            query, context = '', None
        self.context, self.provisional = context, None
        self.last_state, self.had_lock = None, False
        self.worker.ticket += 1
        self.worker.session.find(query)
        self.worker.latest_frame = self.worker.latest_target = self.worker.latest_hand = None
        if self.worker.spatial is not None:
            self.worker.spatial.reset()
        if self.worker.audio is not None:
            self.worker.audio.reset()
            self.worker.audio.player.enable_guidance(context is not None, context=context)
        if query:
            self.worker.latency.start(query, time.monotonic())

    def filter_result(self, job, result):
        """Never give a provisional or obsolete region to the tracker."""
        context = self.context
        session = self.worker.session
        terminal = not (result.get('mode') == 'HYBRID' and result.get('providerPending') is not False)
        if context is None or not context.is_current() or session.inflight is not job:
            if session.inflight is job and terminal:
                session.inflight = None
            return None
        if job.generation != session.generation or job.epoch != session.epoch:
            # A replaced request retains its occupied provider slot until return;
            # its response must not become an error or clarification for the new task.
            if terminal:
                session.inflight = None
            self.provisional = None
            return None
        if result.get('mode') == 'HYBRID' and result.get('providerPending') is not False:
            self.provisional = (job, copy.deepcopy(result))
            return None
        if result.get('mode') == 'HYBRID_VERIFY':
            pending, self.provisional = self.provisional, None
            if result.get('agrees') is True and pending is not None and pending[0] is job:
                result = pending[1]
                result['providerPending'] = False
            else:
                session.inflight = None
                session.needs_clarification = True
                self.coordinator.report(context, AgentResult('object-finding', 'clarify',
                    'I could not verify that target. Please describe it more specifically.'))
                return None
        verdict = validate_object_result(job, result, current_generation=session.generation,
                                         current_epoch=session.epoch, now=time.monotonic())
        if not self.coordinator.report(context, verdict):
            session.inflight = None
            return None
        if verdict.status != 'handoff':
            session.inflight = None
            session.needs_clarification = True
            session.tracker = None
            session.state = 'AMBIGUOUS' if verdict.status == 'clarify' else 'ERROR'
            session.detail = verdict.text
            return None
        return result

    def observe(self, packet):
        self.frames.publish(packet)

    def tracking(self, now):
        context = self.context
        if context is None or not context.is_current():
            return
        packet = self.worker.latest_frame
        received = packet.stamp.received_monotonic_s if packet is not None else None
        if (self.had_lock or self.worker.session.state == 'LOCKED') and (
                received is None or not 0 <= now - received <= .3):
            self.coordinator.report_tracking(context, 'lost', observed_at=received)
            return
        state = self.worker.session.state
        if state == self.last_state:
            return
        self.last_state = state
        if packet is not None:
            self.frames.event('tracking', packet.stamp.frame_id, state)
        if state == 'LOCKED':
            self.had_lock = True
            self.coordinator.report_tracking(context, 'tracking',
                target={'label': self.worker.session.label}, observed_at=received)
        elif self.had_lock and state in ('TARGET LOST', 'HIDDEN', 'UNCERTAIN', 'ERROR'):
            self.coordinator.report_tracking(context, 'lost', observed_at=received)
        elif state == 'ERROR':
            self.coordinator.report(context, AgentResult('local-tracker', 'error',
                'The local tracker could not acquire that target. Please try again.', observed_at=received))

    def close(self):
        self.coordinator.close()
        self.frames.clear()
