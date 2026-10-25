"""Bounded, cancellable intent orchestration. Never emits physical guidance."""
from __future__ import annotations

from collections import deque
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
import re
import threading
import time
import uuid
from typing import Callable, Any


@dataclass(frozen=True)
class TaskContext:
    session_id: str
    task_id: str
    generation: int
    created_at: float
    cancelled: threading.Event = field(compare=False, repr=False)
    _current: Callable[[], bool] = field(compare=False, repr=False)

    def is_current(self):
        return not self.cancelled.is_set() and self._current()

    def check(self):
        if not self.is_current():
            raise CancelledError()


@dataclass(frozen=True)
class Route:
    action: str
    query: str = ''
    message: str = ''


@dataclass(frozen=True)
class AgentResult:
    agent: str
    status: str
    text: str = ''
    target: Any = None
    observed_at: float | None = None
    frame_id: str | None = None


def direct_route(text):
    """Only unmistakable commands bypass the intent provider."""
    normalized = ' '.join(text.lower().strip(' .!?').split())
    if normalized in ('stop', 'cancel', 'stop tracking', 'stop guiding', 'pause', 'be quiet', 'stop talking'):
        return Route('stop')
    if normalized in ('describe', 'describe surroundings', 'describe the surroundings', "what's around me", 'what is around me', 'what do you see'):
        return Route('describe', text)
    if normalized in ('what did i miss', 'what just happened'):
        return Route('recall', text)
    match = re.fullmatch(r'(?:find|find me|locate|track)\s+(.+)', normalized)
    if match:
        return Route('find', match.group(1))
    return None


class Coordinator:
    """One daemon worker, one replaceable pending request, bounded telemetry/history.

    Tools return AgentResult, or None for an explicit asynchronous handoff followed
    by report(). All side-effecting tools must check the context at dispatch too.
    Stop is synchronous and independent of the worker and cloud providers.
    """
    def __init__(self, router, find, describe, recall, stop_local, speak,
                 timeout_s=5., clock=time.monotonic):
        if not 0 < timeout_s <= 5:
            raise ValueError('Agent timeout must be in (0, 5] seconds')
        self.router = router
        self.tools = dict(find=find, describe=describe, recall=recall)
        self.stop_local, self.speak = stop_local, speak
        self.timeout_s, self.clock = timeout_s, clock
        self.session_id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = False
        self.generation = 0
        self.current = None
        self.pending = None
        self.dialogue = deque(maxlen=12)
        self.events = deque(maxlen=64)
        self.goal = ''
        self.target = None
        self.status = 'idle'
        self.agent = 'coordinator'
        self.error = None
        self.clarification = ''
        self.observed_at = None
        self.deadline = None
        self.response = ''
        self.latency_ms = None
        self.thread = threading.Thread(target=self._run, daemon=True, name='visionos-coordinator')
        self.thread.start()
        self.watchdog = threading.Thread(target=self._watch, daemon=True, name='visionos-agent-deadline')
        self.watchdog.start()

    def _event(self, kind, **details):
        self.events.append(dict(kind=kind, at=self.clock(), task_id=self.current.task_id if self.current else None,
                                generation=self.generation, **details))

    def _valid(self, task_id, generation):
        with self.lock:
            return not self.closed and self.current is not None and self.current.task_id == task_id and self.generation == generation

    def submit(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError('Request must contain 1–1000 characters')
        text = text.strip()
        direct = direct_route(text)
        if direct and direct.action == 'stop':
            self.stop()
            return None
        with self.lock:
            if self.closed:
                raise RuntimeError('Coordinator closed')
            if self.current:
                self.current.cancelled.set()
            self.generation += 1
            task_id, generation = uuid.uuid4().hex, self.generation
            context = TaskContext(self.session_id, task_id, generation, self.clock(), threading.Event(),
                                  lambda: self._valid(task_id, generation))
            self.current = context
            self.stop_local()
            # Preserve clarification and prior goal in router state until routing.
            self.pending = (text, direct if not self.clarification else None, context)
            self.status, self.agent, self.error = 'queued', 'coordinator', None
            self.response, self.latency_ms = '', None
            self.deadline = self.clock() + self.timeout_s
            self._event('request')
            self.wake.set()
        return context

    def stop(self, reason='Stopped'):
        with self.lock:
            if self.current:
                self.current.cancelled.set()
            self.generation += 1
            self.pending = None
            self.deadline = None
            self.status, self.agent, self.target = 'stopped', 'coordinator', None
            self.goal, self.clarification = '', ''
            self.response, self.error = '', None
            self._event('cancelled', reason=str(reason)[:160])
            self.stop_local()
        self.wake.set()

    def report(self, context, result):
        if not isinstance(result, AgentResult):
            raise TypeError('Tools must return AgentResult')
        with self.lock:
            if not context.is_current():
                self._event('obsolete_result', agent=result.agent)
                return False
            if self.deadline is not None and self.clock() >= self.deadline:
                context.cancelled.set()
                self.deadline, self.target = None, None
                self.status, self.error = 'error', 'PROVIDER_TIMEOUT'
                self._event('error', error=self.error)
                self.stop_local()
                return False
            self.agent, self.status = result.agent, result.status
            self.response = result.text
            self.latency_ms = round((self.clock()-context.created_at)*1000)
            if result.status not in ('pending', 'running'):
                self.deadline = None
            self.target, self.observed_at = result.target, result.observed_at
            self.clarification = result.text if result.status in ('clarify', 'ambiguous') else ''
            self._event('result', agent=result.agent, status=result.status,
                        latency_ms=round((self.clock()-context.created_at)*1000), frame_id=result.frame_id)
            if result.text:
                self.dialogue.append(dict(role='assistant', content=result.text[:2000]))
                # This callback only queues audio; it must never synthesize here.
                self.speak(result.text, context)
            return True

    def snapshot(self):
        with self.lock:
            return dict(session_id=self.session_id, task_id=self.current.task_id if self.current else None,
                        generation=self.generation, goal=self.goal, status=self.status, agent=self.agent,
                        target=self.target, observed_at=self.observed_at, error=self.error,
                        clarification=self.clarification, response=self.response, latency_ms=self.latency_ms,
                        events=list(self.events))

    def report_tracking(self, context, status, target=None, observed_at=None):
        """Local tracker supplies evidence/status; this never chooses a cue."""
        with self.lock:
            if not context.is_current():
                return False
            if status.lower() in ('lost', 'target lost', 'target_lost', 'error'):
                self.stop('Target lost')
                self.status = 'target_lost'
                self.observed_at = observed_at
                return True
        return self.report(context, AgentResult('local-tracker', status, target=target, observed_at=observed_at))

    def _watch(self):
        while True:
            time.sleep(.05)
            with self.lock:
                if self.closed:
                    return
                if self.deadline is None or self.clock() < self.deadline or self.current is None:
                    continue
                self.current.cancelled.set()
                self.pending, self.deadline, self.target = None, None, None
                self.status, self.error = 'error', 'PROVIDER_TIMEOUT'
                self._event('error', error=self.error)
                self.stop_local()

    def _run(self):
        while True:
            self.wake.wait(.1)
            self.wake.clear()
            with self.lock:
                if self.closed:
                    return
                job, self.pending = self.pending, None
            if job is None:
                continue
            text, route, context = job
            try:
                context.check()
                with self.lock:
                    context.check()
                    state = dict(goal=self.goal, clarification=self.clarification, target=self.target)
                    dialogue = list(self.dialogue)
                    self.status = 'routing'
                    self.dialogue.append(dict(role='user', content=text))
                if route is None:
                    route = self.router.route(text, dialogue, state)
                context.check()
                if self.clock()-context.created_at > self.timeout_s:
                    raise TimeoutError()
                if not isinstance(route, Route) or route.action not in (*self.tools, 'clarify'):
                    raise ValueError('Invalid coordinator tool')
                with self.lock:
                    context.check()
                    self.goal = route.query or text
                    self.clarification = ''
                    self.agent = {'find': 'object-finding', 'describe': 'scene-description', 'recall': 'social-context'}.get(route.action, 'coordinator')
                    self.status = 'running'
                    self.deadline = self.clock() + self.timeout_s
                    self._event('handoff', source='coordinator', destination=self.agent)
                if route.action == 'clarify':
                    result = AgentResult('coordinator', 'clarify', route.message)
                else:
                    result = self.tools[route.action](route.query or text, context)
                context.check()
                if result is not None:
                    self.report(context, result)
            except CancelledError:
                pass
            except Exception as exc:
                with self.lock:
                    if context.is_current():
                        # Provider raw errors can contain transcripts or credentials.
                        self.error = 'PROVIDER_TIMEOUT' if isinstance(exc, TimeoutError) else type(exc).__name__
                        self.status = 'error'
                        self.deadline = None
                        self._event('error', error=self.error)
                        self.stop_local()
                        self.speak('That request could not be completed. Please try again.', context)

    def close(self):
        self.stop('Session ended')
        with self.lock:
            self.closed = True
        self.wake.set()
