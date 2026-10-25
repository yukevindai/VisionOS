"""Isolated menu-to-speech bridge. Produces text requests; never owns audio or a camera."""
from dataclasses import asdict, dataclass
import hashlib
import time
from typing import Callable, Protocol
import uuid

from .questions import MenuSession, reading_chunks


@dataclass(frozen=True)
class MenuSpeechRequest:
    event_id: str
    session_id: str
    generation: int  # Exact owner-supplied SessionContext.generation, not a fabricated task step.
    reading_generation: int  # Menu queue revision; independent of physical task generation.
    menu_id: str
    text: str
    source_ids: tuple[str, ...]
    priority: int
    kind: str
    audience: str
    user_confirmed: bool
    created_at_ms: int
    expires_at_ms: int

    def to_dict(self):
        return asdict(self)


class MenuSpeechScheduler(Protocol):
    def enqueue(self, request: MenuSpeechRequest) -> bool: ...
    def cancel_menu(self, session_id: str, generation: int, reading_generation: int) -> None: ...


class CallbackScheduler:
    """Concrete callback bridge for an owner-provided existing speech scheduler binding."""
    def __init__(self, enqueue: Callable[[MenuSpeechRequest], bool],
                 cancel_menu: Callable[[str, int, int], None]):
        self.enqueue, self.cancel_menu = enqueue, cancel_menu


class MenuSpeechAdapter:
    def __init__(self, menu, session_id: str, generation: int, scheduler=None, clock=None):
        if not session_id or generation < 0:
            raise ValueError('Supply the actual owner session and generation')
        self.session = MenuSession(menu)
        self.session_id, self.generation = session_id, generation
        self.menu_id = hashlib.sha256('|'.join(p['pageId'] for p in menu['pages']).encode()).hexdigest()[:16]
        self.scheduler: MenuSpeechScheduler | None = scheduler
        self.clock = clock or (lambda: time.monotonic_ns() // 1_000_000)
        self.reading_generation, self.index, self.paused = 0, 0, False
        self.chunks, self.pending, self.released = [], None, False
        self.draft_sources = ()

    def _invalidate(self):
        if self.scheduler:
            self.scheduler.cancel_menu(self.session_id, self.generation, self.reading_generation)
        self.reading_generation += 1
        self.pending, self.released = None, False

    def _prepare(self, text, sources, kind, audience='user', confirmed=False):
        now = self.clock()
        self.pending = MenuSpeechRequest(uuid.uuid4().hex, self.session_id, self.generation,
            self.reading_generation, self.menu_id, text, tuple(dict.fromkeys(sources)), 4,
            kind, audience, confirmed, now, now + 30_000)
        self.released = False
        return self.pending

    def start_reading(self, section=None):
        """Explicit selection only; does not enqueue or automatically start playback."""
        self._invalidate()
        self.chunks = list(reading_chunks(self.session.menu, section))
        self.index, self.paused = 0, False

    def prepare_next(self):
        if self.paused or self.index >= len(self.chunks):
            return None
        if self.pending:
            return self.pending
        sources = [word['id'] for page in self.session.menu['pages'] for word in page['words']]
        return self._prepare(self.chunks[self.index], sources, 'menu-reading')

    def prepare_answer(self, question, *, staff_confirmed=False):
        """Answers solely from the existing menu cache; does not scan or call Gemini."""
        command = question.strip().casefold()
        if command in ('pause', 'stop', 'resume', 'skip'):
            getattr(self, command)()
            return dict(text='Reading '+command+'.', status='READING_CONTROL', sourceIds=[]), None
        self._invalidate()
        self.paused = False
        answer = self.session.answer(question)
        if answer.get('status') == 'DRAFT':
            self.draft_sources = tuple(answer.get('sourceIds', ()))
        if answer.get('requiresSpeechDispatch'):
            if not staff_confirmed:
                return answer, None
            return answer, self._prepare(answer['text'], self.draft_sources,
                'order-communication', 'staff', True)
        return answer, self._prepare(answer['text'], answer.get('sourceIds', ()), 'menu-answer')

    def release(self, request):
        """Caller explicitly dispatches one request; scheduler remains the output authority."""
        now = self.clock()
        if (not isinstance(request, MenuSpeechRequest) or self.scheduler is None or self.paused or self.released or request != self.pending or
            request.session_id != self.session_id or request.generation != self.generation or
            request.reading_generation != self.reading_generation or request.priority != 4 or
            not request.text or not request.created_at_ms <= now < request.expires_at_ms or
            request.audience == 'staff' and not request.user_confirmed):
            return False
        self.released = bool(self.scheduler.enqueue(request))
        return self.released

    def completed(self, event_id):
        """Only an actual scheduler completion acknowledges a spoken chunk."""
        if not self.pending or not self.released or self.pending.event_id != event_id:
            return False
        if self.pending.kind == 'menu-reading':
            self.index += 1
        self.pending, self.released = None, False
        return True

    def pause(self):
        self._invalidate()
        self.paused = True

    def resume(self):
        self.paused = False  # Explicit prepare/release still required; restart interrupted chunk.

    def skip(self):
        self._invalidate()
        self.index = min(len(self.chunks), self.index + 1)

    def stop(self):
        self._invalidate()
        self.index = len(self.chunks)
        self.paused = True

    cancel = stop

    def update_context(self, session_id, generation):
        if not session_id or generation < 0:
            raise ValueError('Invalid owner context')
        self._invalidate()
        self.session_id, self.generation = session_id, generation
