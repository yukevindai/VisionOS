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

Wiring (in the host pipeline):
    listener = FamilyNotesListener.from_env(say=player.say, speaking=player.speaking,
                                            is_idle=<no active task and no pending request>, log=log)
    if listener: listener.start()
    ...after a find task succeeds:  listener.announce_for_target(label, place=None)
    ...on shutdown:                 listener.close()
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict

DEVICE_KEY = re.compile(r'^vfn_[A-Za-z0-9_-]{43}$')
TICK_S = 0.2
START_TIMEOUT_S = 8.0     # say() accepted but audio never started (synthesis failed or was invalidated)
RECONNECT_S = (1, 2, 5, 10, 30)
MAX_TEXT = 400


class FamilyNotesListener:
    def __init__(self, client, device_key, *, say, speaking, is_idle, log=None, clock=time.monotonic, autostart=False):
        if not DEVICE_KEY.match(device_key or ''):
            raise ValueError('FAMILY_NOTES_DEVICE_KEY is not a VisionOS device key')
        self.client = client
        self.key = device_key
        self.say = say
        self.speaking = speaking
        self.is_idle = is_idle
        self.log = log or (lambda *_a, **_k: None)
        self.clock = clock
        self.lock = threading.Lock()
        self.queue: OrderedDict[str, str] = OrderedDict()   # noteId -> spoken text (unheard, oldest first)
        self.extra: list[str] = []                            # object-related reminders (not tracked as unheard)
        self.current = None                                    # (kind, id_or_none, text, started_at, began_playing)
        self.paired = None
        self.stopped = threading.Event()
        self.threads = []
        if autostart:
            self.start()

    @classmethod
    def from_env(cls, *, say, speaking, is_idle, log=None, env=os.environ, client_factory=None):
        """Returns None (feature off) unless both CONVEX_URL and FAMILY_NOTES_DEVICE_KEY are set."""
        url = (env.get('FAMILY_NOTES_CONVEX_URL') or env.get('CONVEX_URL') or '').strip()
        key = (env.get('FAMILY_NOTES_DEVICE_KEY') or '').strip()
        if not url or not key:
            return None
        if client_factory is None:
            from convex import ConvexClient  # pip install convex (see requirements-family-notes.txt)
            client_factory = ConvexClient
        return cls(client_factory(url), key, say=say, speaking=speaking, is_idle=is_idle, log=log)

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
            added = [i for i in fresh if i not in self.queue]
            self.queue = fresh  # server is the source of truth: heard or deleted notes drop out
            if added:
                self.log('FAMILY_NOTES_RECEIVED', count=len(added))

    def announce_for_target(self, label, place=None):
        """After a find task succeeds: queue notes about that object (warnings first). Returns the count."""
        args = {'deviceKey': self.key, 'label': str(label)[:160]}
        if place:
            args['place'] = str(place)[:40]
        try:
            hits = self.client.query('notes:forObjectByDevice', args)
        except Exception as exc:  # network or server error: the task itself already succeeded
            self.log('FAMILY_NOTES_LOOKUP_FAILED', error=type(exc).__name__)
            return 0
        if not hits:
            return 0
        with self.lock:
            for hit in hits:
                text = clean(hit.get('spoken', ''))
                note_id = str(hit.get('noteId', ''))
                if not text or note_id in self.queue:  # unheard notes are already queued
                    continue
                self.extra.append(text)
        self.log('FAMILY_NOTES_FOR_TARGET', count=len(hits))
        return len(hits)

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


def clean(text):
    return ' '.join(''.join(ch for ch in str(text) if ch.isprintable()).split())[:MAX_TEXT]
