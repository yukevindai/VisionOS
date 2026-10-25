"""Family notes on the laptop demo: speak notes from trusted helpers between tasks.

A helper (for example the user's daughter) leaves a note on the VisionOS Family Notes page.
Convex pushes it to this process through a live subscription (`notes:forDevice`); this
listener speaks it only while the pipeline is idle, through the pipeline's existing audio
player, so Stop, hazards and hand guidance always win. A note is marked heard
(`notes:markHeardByDevice`) only after it played to the end while still idle; an
interrupted note is spoken again later.

Notes are information from a person, never commands: nothing here starts guidance,
confirms a press or authorizes payment. The device key comes from the owner's
"Pair this laptop" button and lives only in backend/.env (FAMILY_NOTES_DEVICE_KEY).

Object notes are the exception to "only while idle": when a find task locks onto an
object, on_target_locked() looks up notes about it and speaks them right away, before hand
guidance starts (a warning about the bottle is useful before the hand reaches it). While
holding() is True the host keeps guidance audio quiet; Stop or a new request ends it.

Wiring (in the host pipeline; live_tracking.py does this):
    say, say_now = speech_adapters(player)
    listener = FamilyNotesListener.from_env(say=say, speaking=player.speaking,
                                            is_idle=<no active task and no pending request>, log=log)
    if listener: listener.start()
    ...when a find task first locks:  listener.on_target_locked(label, say=say_now, is_current=...)
    ...every frame:                   skip guidance audio while listener.holding()
    ...on shutdown:                   listener.close()
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace

DEVICE_KEY = re.compile(r'^vfn_[A-Za-z0-9_-]{43}$')
TICK_S = 0.2
START_TIMEOUT_S = 8.0     # say() accepted but audio never started (synthesis failed or was invalidated)
RECONNECT_S = (1, 2, 5, 10, 30)
MAX_TEXT = 400
TARGET_LOOKUP_WAIT_S = 1.5   # guidance waits at most this long for the object-note lookup
TARGET_NOTES = 1             # object notes spoken before guidance: the most important one (warnings first)
AUDIO_TIMEOUT_S = 4.0        # fetching a note's rendered WAV (the helper's own voice) from Convex storage
MAX_AUDIO_BYTES = 4 * 1024 * 1024


class FamilyNotesListener:
    def __init__(self, client, device_key, *, say, speaking, is_idle, log=None, clock=time.monotonic, autostart=False,
                 prepare_audio=None, fetch_audio=None):
        if not DEVICE_KEY.match(device_key or ''):
            raise ValueError('FAMILY_NOTES_DEVICE_KEY is not a VisionOS device key')
        self.client = client
        self.key = device_key
        self.say = say
        self.speaking = speaking
        self.is_idle = is_idle
        self.log = log or (lambda *_a, **_k: None)
        self.clock = clock
        self.lock = threading.RLock()   # re-entrant: helpers like _remember_url run with and without it held
        self.queue: OrderedDict[str, str] = OrderedDict()   # noteId -> spoken text (unheard, oldest first)
        self.extra: list[str] = []                            # object-related reminders (not tracked as unheard)
        self.current = None                                    # (kind, id_or_none, text, started_at, began_playing)
        self.paired = None
        self.target = None     # object notes spoken at lock time (see on_target_locked)
        # Family Voice: a note's WAV rendered by Convex (often in the helper's own voice). prepare_audio hands
        # it to the player for that exact text; without it (or if the download fails) the text is synthesized.
        self.prepare_audio = prepare_audio
        self.fetch_audio = fetch_audio or fetch_wav
        self.urls: dict[str, str] = {}      # spoken text -> audio URL from Convex
        self.audio_cache: OrderedDict[str, bytes] = OrderedDict()
        self.receipt_thread = None
        self.stopped = threading.Event()
        self.threads = []
        if autostart:
            self.start()

    @staticmethod
    def configured(env=os.environ):
        """True when both CONVEX_URL (or FAMILY_NOTES_CONVEX_URL) and FAMILY_NOTES_DEVICE_KEY are set."""
        url = (env.get('FAMILY_NOTES_CONVEX_URL') or env.get('CONVEX_URL') or '').strip()
        return bool(url and (env.get('FAMILY_NOTES_DEVICE_KEY') or '').strip())

    @classmethod
    def from_env(cls, *, say, speaking, is_idle, log=None, env=os.environ, client_factory=None, prepare_audio=None):
        """Returns None (feature off) unless both CONVEX_URL and FAMILY_NOTES_DEVICE_KEY are set."""
        url = (env.get('FAMILY_NOTES_CONVEX_URL') or env.get('CONVEX_URL') or '').strip()
        key = (env.get('FAMILY_NOTES_DEVICE_KEY') or '').strip()
        if not url or not key:
            return None
        if client_factory is None:
            from convex import ConvexClient  # pip install convex (see requirements-family-notes.txt)
            client_factory = ConvexClient
        return cls(client_factory(url), key, say=say, speaking=speaking, is_idle=is_idle, log=log,
                   prepare_audio=prepare_audio)

    # ----- lifecycle -----

    def start(self):
        for target in (self._subscribe_loop, self._speak_loop):
            thread = threading.Thread(target=target, name=f'family-notes-{target.__name__}', daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def close(self):
        self.stopped.set()

    # ----- inputs -----

    def update(self, value):
        """Apply one live result of notes:forDevice (None means unknown or revoked device)."""
        with self.lock:
            if value is None:
                if self.paired is not False:
                    self.log('FAMILY_NOTES_UNPAIRED')
                self.paired = False
                self.queue.clear()
                self.extra.clear()
                return
            if self.paired is not True:
                self.log('FAMILY_NOTES_PAIRED', device=str(value.get('deviceLabel', ''))[:40])
            self.paired = True
            fresh = OrderedDict()
            for note in value.get('notes', []):
                note_id, text = str(note.get('noteId', '')), clean(note.get('spoken', ''))
                if note_id and text:
                    fresh[note_id] = text
                    self._remember_url(text, note.get('audioUrl'))
            added = [i for i in fresh if i not in self.queue]
            self.queue = fresh  # server is the source of truth: heard or deleted notes drop out
            if added:
                self.log('FAMILY_NOTES_RECEIVED', count=len(added))

    def _lookup(self, label, place):
        args = {'deviceKey': self.key, 'label': str(label)[:160]}
        if place:
            args['place'] = str(place)[:40]
        try:
            return self.client.query('notes:forObjectByDevice', args) or []
        except Exception as exc:  # network or server error: the task itself already succeeded
            self.log('FAMILY_NOTES_LOOKUP_FAILED', error=type(exc).__name__)
            return []

    def announce_for_target(self, label, place=None):
        """Queue notes about an object to be spoken at the next idle moment. Returns the count."""
        hits = self._lookup(label, place)
        if not hits:
            return 0
        with self.lock:
            for hit in hits:
                text = clean(hit.get('spoken', ''))
                note_id = str(hit.get('noteId', ''))
                if not text or note_id in self.queue:  # unheard notes are already queued
                    continue
                self.extra.append(text)
                self._remember_url(text, hit.get('audioUrl'))
        self.log('FAMILY_NOTES_FOR_TARGET', count=len(hits))
        return len(hits)

    def on_target_locked(self, label, *, say, is_current, place=None, background=True):
        """A find task just locked onto `label`: speak notes about it now, before hand guidance.

        The lookup runs off the caller's thread. holding() is True from this call until the notes
        finished, the lookup found nothing (or took longer than TARGET_LOOKUP_WAIT_S), or
        is_current() turned False (Stop or a new request). Unheard notes spoken here are marked heard."""
        token = object()
        with self.lock:
            self.target = dict(token=token, state='lookup', started=self.clock(), ids=[], is_current=is_current)

        def work():
            hits = self._lookup(label, place)[:TARGET_NOTES]
            texts = [clean(h.get('spoken', '')) for h in hits]
            text = clean(' '.join(t for t in texts if t))
            if len(hits) == 1:
                self._remember_url(text, hits[0].get('audioUrl'))
                self._prepare(text)
            with self.lock:
                if self.target is None or self.target['token'] is not token:
                    return
                if not text or not is_current():
                    self.target = None
                    return
                unheard = [str(h.get('noteId', '')) for h in hits if not h.get('heard')]
            started = say(text, name='FAMILY_NOTE', is_current=is_current)
            with self.lock:
                if self.target is None or self.target['token'] is not token:
                    return
                if started:
                    self.target.update(state='starting', started=self.clock(), ids=unheard)
                else:
                    self.target = None
            self.log('FAMILY_NOTES_FOR_TARGET', count=len(hits), spoken=bool(started))

        if background:
            threading.Thread(target=work, name='family-notes-target', daemon=True).start()
        else:
            work()

    def holding(self):
        """True while object notes from on_target_locked are being looked up or spoken. Never blocks."""
        with self.lock:
            target = self.target
        if target is None:
            return False
        now = self.clock()
        state, done = target['state'], False
        if not target['is_current']():
            done = True
        elif state == 'lookup':
            done = now - target['started'] > TARGET_LOOKUP_WAIT_S
        elif state == 'starting':
            if self.speaking():
                target['state'] = 'playing'
            else:
                done = now - target['started'] > START_TIMEOUT_S
        elif not self.speaking():  # playing -> finished
            done = True
            heard = target['ids']
            if heard:  # receipts go over the network: never on the caller's (frame loop) thread
                self.receipt_thread = threading.Thread(target=self._mark_heard, args=(heard,), daemon=True)
                self.receipt_thread.start()
        if done:
            with self.lock:
                if self.target is target:
                    self.target = None
            return False
        return True

    def _mark_heard(self, note_ids):
        for note_id in note_ids:
            try:
                self.client.mutation('notes:markHeardByDevice', {'deviceKey': self.key, 'noteId': note_id})
                with self.lock:
                    self.queue.pop(note_id, None)
            except Exception as exc:
                self.log('FAMILY_NOTE_RECEIPT_FAILED', error=type(exc).__name__)

    # ----- Family Voice audio -----

    def _remember_url(self, text, url):
        if url and isinstance(url, str) and url.startswith('https://') and text:
            with self.lock:
                self.urls[text] = url
                while len(self.urls) > 50:
                    self.urls.pop(next(iter(self.urls)))

    def _prepare(self, text):
        """Before saying `text`: download its rendered WAV (cached) and hand it to the player. Never raises."""
        if self.prepare_audio is None:
            return False
        with self.lock:
            url = self.urls.get(text)
            audio = self.audio_cache.get(url) if url else None
        if not url:
            return False
        if audio is None:
            try:
                audio = self.fetch_audio(url)
            except Exception as exc:  # offline or expired URL: the text is synthesized instead
                self.log('FAMILY_NOTE_AUDIO_FAILED', error=type(exc).__name__)
                return False
            with self.lock:
                self.audio_cache[url] = audio
                while len(self.audio_cache) > 10:
                    self.audio_cache.popitem(last=False)
        self.prepare_audio(text, audio)
        self.log('FAMILY_NOTE_AUDIO_READY', bytes=len(audio))
        return True

    # ----- speaking -----

    def tick(self):
        """One step of the speak state machine. Safe to call from any loop; never blocks."""
        now = self.clock()
        with self.lock:
            current = self.current
        if current is None:
            if not self.is_idle() or self.speaking():
                return
            with self.lock:
                if self.extra:
                    item = ('extra', None, self.extra.pop(0))
                elif self.queue:
                    note_id, text = next(iter(self.queue.items()))
                    item = ('note', note_id, text)
                else:
                    return
            self._prepare(item[2])
            if self.say(item[2], name='FAMILY_NOTE', is_current=self.is_idle):
                with self.lock:
                    self.current = (*item, now, False)
                self.log('FAMILY_NOTE_SPEAKING', kind=item[0])
            elif item[0] == 'extra':  # busy player: try again on a later tick
                with self.lock:
                    self.extra.insert(0, item[2])
            return

        kind, note_id, text, started, playing = current
        if not self.is_idle():
            # A task, Stop or guidance took over. The player already cut the note; speak it again later.
            self._finish(heard=False, reason='interrupted')
            if kind == 'extra':
                with self.lock:
                    self.extra.insert(0, text)
            return
        if self.speaking():
            if not playing:
                with self.lock:
                    self.current = (kind, note_id, text, started, True)
            return
        if not playing:
            if now - started > START_TIMEOUT_S:
                self._finish(heard=False, reason='never_started')
            return
        self._finish(heard=True, reason='done')

    def _finish(self, *, heard, reason):
        with self.lock:
            kind, note_id, _text, _started, _playing = self.current
            self.current = None
        self.log('FAMILY_NOTE_FINISHED', kind=kind, heard=heard, reason=reason)
        if heard and kind == 'note':
            try:
                self.client.mutation('notes:markHeardByDevice', {'deviceKey': self.key, 'noteId': note_id})
                with self.lock:
                    self.queue.pop(note_id, None)
            except Exception as exc:  # it stays unheard on the server and will be spoken again
                self.log('FAMILY_NOTE_RECEIPT_FAILED', error=type(exc).__name__)

    def _speak_loop(self):
        while not self.stopped.wait(TICK_S):
            try:
                self.tick()
            except Exception as exc:  # never take the pipeline down
                self.log('FAMILY_NOTES_ERROR', error=type(exc).__name__)

    def _subscribe_loop(self):
        attempt = 0
        while not self.stopped.is_set():
            try:
                for value in self.client.subscribe('notes:forDevice', {'deviceKey': self.key}):
                    attempt = 0
                    self.update(value)
                    if self.stopped.is_set():
                        return
            except Exception as exc:
                self.log('FAMILY_NOTES_DISCONNECTED', error=type(exc).__name__)
            delay = RECONNECT_S[min(attempt, len(RECONNECT_S) - 1)]
            attempt += 1
            if self.stopped.wait(delay):
                return


def fetch_wav(url, timeout=AUDIO_TIMEOUT_S):
    """Download a note's WAV from Convex storage (a signed https URL). Raises on anything unexpected."""
    import urllib.request
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept': 'audio/wav'}), timeout=timeout) as res:
        data = res.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('not a WAV note')
    return data


class NoteAudio:
    """Speech provider for the player: returns a prepared note WAV for exactly that text, else defers.

    Installed in front of the player's own provider (Cartesia, or none for the Windows voice), so the
    player keeps one audio path, Stop and priorities, and plays the helper's own voice when it has one."""

    def __init__(self, inner=None):
        self.inner = inner
        self.lock = threading.Lock()
        self.prepared: OrderedDict[str, bytes] = OrderedDict()

    def prepare(self, text, audio):
        with self.lock:
            self.prepared[clean(text)] = audio
            while len(self.prepared) > 8:
                self.prepared.popitem(last=False)

    def synthesize(self, text):
        with self.lock:
            audio = self.prepared.pop(clean(text), None)
        if audio is not None:
            return audio
        if self.inner is None:
            raise RuntimeError('no speech provider')  # the player falls back to the Windows voice
        return self.inner.synthesize(text)


def speech_adapters(player):
    """(say, say_now, prepare_audio) for the pipeline's player: a ClipPlayer, or PriorityAudio (--agents).

    say follows the player's priorities (used for idle notes). say_now stops guidance cues and
    speaks at once (used only for object notes at lock time). Both take is_current like ClipPlayer.
    prepare_audio(text, wav) makes the next say(text) play that WAV (Family Voice)."""
    raw = player
    try:
        from .agent_audio import PriorityAudio
    except ImportError:  # pragma: no cover
        PriorityAudio = ()
    if PriorityAudio and isinstance(player, PriorityAudio):
        raw = player.player

        def say(text, name='FAMILY_NOTE', is_current=lambda: True):
            return player.say(text, name=name, context=SimpleNamespace(is_current=is_current, cancelled=None))
    else:
        say = player.say

    def say_now(text, name='FAMILY_NOTE', is_current=lambda: True):
        raw.stop()
        return raw.say(text, name=name, is_current=is_current)

    provider = getattr(raw, 'speech_provider', None)
    if not isinstance(provider, NoteAudio):
        provider = NoteAudio(provider)
        raw.speech_provider = provider
    return say, say_now, provider.prepare


def clean(text):
    return ' '.join(''.join(ch for ch in str(text) if ch.isprintable()).split())[:MAX_TEXT]
