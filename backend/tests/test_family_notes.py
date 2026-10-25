"""Family notes listener on the laptop. SIMULATED: fake Convex client, fake player, fake clock."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from types import SimpleNamespace  # noqa: E402

from r3_vision.family_notes import (START_TIMEOUT_S, TARGET_LOOKUP_WAIT_S, FamilyNotesListener,  # noqa: E402
                                    clean, speech_adapters)

KEY = 'vfn_' + 'A' * 43


class FakeClient:
    def __init__(self):
        self.mutations, self.queries, self.query_result = [], [], []

    def mutation(self, name, args):
        self.mutations.append((name, args))
        return True

    def query(self, name, args):
        self.queries.append((name, args))
        return self.query_result


class FakePlayer:
    """say() starts audio on the next tick; finish() ends it; interrupt() is what a new cue or Stop does."""

    def __init__(self):
        self.said, self.playing, self.accept = [], False, True

    def say(self, text, name='SPEECH', is_current=lambda: True):
        if not self.accept or not is_current():
            return False
        self.said.append(text)
        self.playing = True
        return True

    def speaking(self):
        return self.playing

    def finish(self):
        self.playing = False


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def note(i, text):
    return {'noteId': f'n{i}', 'kind': 'info', 'createdAt': i, 'spoken': text}


class FamilyNotesTests(unittest.TestCase):
    def setUp(self):
        self.client, self.player, self.clock, self.idle, self.events = FakeClient(), FakePlayer(), Clock(), [True], []
        self.listener = FamilyNotesListener(self.client, KEY, say=self.player.say, speaking=self.player.speaking,
                                            is_idle=lambda: self.idle[0], clock=self.clock,
                                            log=lambda event, **kw: self.events.append(event))

    def play_through(self):
        self.listener.tick()   # say()
        self.listener.tick()   # sees audio playing
        self.player.finish()
        self.listener.tick()   # finished while idle -> heard

    def test_speaks_oldest_note_when_idle_and_marks_it_heard_after_it_finished(self):
        self.listener.update({'deviceLabel': 'Laptop', 'notes': [note(1, 'Note from Lan: first'), note(2, 'Note from Lan: second')]})
        self.listener.tick()
        self.assertEqual(self.player.said, ['Note from Lan: first'])
        self.assertEqual(self.client.mutations, [])  # not heard until it played to the end
        self.listener.tick()
        self.player.finish()
        self.listener.tick()
        self.assertEqual(self.client.mutations, [('notes:markHeardByDevice', {'deviceKey': KEY, 'noteId': 'n1'})])
        self.play_through()
        self.assertEqual(self.player.said[-1], 'Note from Lan: second')

    def test_waits_while_a_task_is_running(self):
        self.idle[0] = False
        self.listener.update({'notes': [note(1, 'Note from Lan: hi')]})
        for _ in range(5):
            self.listener.tick()
        self.assertEqual(self.player.said, [])
        self.idle[0] = True
        self.listener.tick()
        self.assertEqual(self.player.said, ['Note from Lan: hi'])

    def test_never_talks_over_other_audio(self):
        self.player.playing = True  # e.g. a Cartesia answer is playing
        self.listener.update({'notes': [note(1, 'Note from Lan: hi')]})
        self.listener.tick()
        self.assertEqual(self.player.said, [])

    def test_interrupted_note_is_not_heard_and_is_spoken_again(self):
        self.listener.update({'notes': [note(1, 'Note from Lan: hi')]})
        self.listener.tick()
        self.listener.tick()
        self.idle[0] = False          # user said "find my keys" mid-note; guidance cut the note
        self.player.finish()
        self.listener.tick()
        self.assertEqual(self.client.mutations, [])
        self.idle[0] = True
        self.play_through()
        self.assertEqual(self.player.said, ['Note from Lan: hi', 'Note from Lan: hi'])
        self.assertEqual(len(self.client.mutations), 1)

    def test_accepted_but_never_started_times_out_without_a_receipt(self):
        self.listener.update({'notes': [note(1, 'Note from Lan: hi')]})
        self.player.say = lambda text, name='', is_current=None: True  # accepted, audio never begins
        self.listener.say = self.player.say
        self.listener.tick()
        self.clock.t = START_TIMEOUT_S + 1
        self.listener.tick()
        self.assertIn('FAMILY_NOTE_FINISHED', self.events)
        self.assertEqual(self.client.mutations, [])

    def test_revoked_device_stops_everything(self):
        self.listener.update({'notes': [note(1, 'Note from Lan: hi')]})
        self.listener.update(None)
        self.listener.tick()
        self.assertEqual(self.player.said, [])
        self.assertIn('FAMILY_NOTES_UNPAIRED', self.events)

    def test_server_is_the_source_of_truth_for_the_queue(self):
        self.listener.update({'notes': [note(1, 'a'), note(2, 'b')]})
        self.listener.update({'notes': [note(2, 'b')]})  # n1 was heard on the web page meanwhile
        self.listener.tick()
        self.assertEqual(self.player.said, ['b'])

    def test_object_notes_are_spoken_after_a_find_task(self):
        self.client.query_result = [{'noteId': 'w1', 'spoken': 'Safety note from Lan: bleach', 'match': 'warning'},
                                    {'noteId': 'r1', 'spoken': "According to Lan's note: candy", 'match': 'reassurance'}]
        self.assertEqual(self.listener.announce_for_target('blue bottle', 'kitchen'), 2)
        self.assertEqual(self.client.queries[0], ('notes:forObjectByDevice',
                                                  {'deviceKey': KEY, 'label': 'blue bottle', 'place': 'kitchen'}))
        self.play_through()
        self.play_through()
        self.assertEqual(self.player.said, ['Safety note from Lan: bleach', "According to Lan's note: candy"])
        self.assertEqual(self.client.mutations, [])  # reminders are not unheard notes

    def test_lookup_failure_never_breaks_the_task(self):
        def boom(*_):
            raise ConnectionError('offline')
        self.client.query = boom
        self.assertEqual(self.listener.announce_for_target('bottle'), 0)
        self.assertIn('FAMILY_NOTES_LOOKUP_FAILED', self.events)

    def test_from_env_is_off_without_configuration_and_rejects_bad_keys(self):
        kwargs = dict(say=self.player.say, speaking=self.player.speaking, is_idle=lambda: True)
        self.assertIsNone(FamilyNotesListener.from_env(env={}, **kwargs))
        self.assertIsNone(FamilyNotesListener.from_env(env={'CONVEX_URL': 'https://x.convex.cloud'}, **kwargs))
        with self.assertRaises(ValueError):
            FamilyNotesListener.from_env(env={'CONVEX_URL': 'https://x.convex.cloud', 'FAMILY_NOTES_DEVICE_KEY': 'nope'},
                                         client_factory=lambda url: FakeClient(), **kwargs)
        listener = FamilyNotesListener.from_env(env={'CONVEX_URL': 'https://x.convex.cloud', 'FAMILY_NOTES_DEVICE_KEY': KEY},
                                                client_factory=lambda url: FakeClient(), **kwargs)
        self.assertIsNotNone(listener)

    def test_subscription_feeds_updates_and_reconnects_after_errors(self):
        calls = []

        def subscribe(name, args):
            calls.append((name, args))
            if len(calls) == 1:
                raise ConnectionError('socket closed')
            yield {'deviceLabel': 'Laptop', 'notes': [note(1, 'Note from Lan: hi')]}
            self.listener.close()

        self.client.subscribe = subscribe
        import r3_vision.family_notes as module
        self.addCleanup(setattr, module, 'RECONNECT_S', module.RECONNECT_S)
        module.RECONNECT_S = (0,)
        self.listener._subscribe_loop()
        self.assertEqual(calls[0], ('notes:forDevice', {'deviceKey': KEY}))
        self.assertEqual(len(calls), 2)
        self.assertIn('FAMILY_NOTES_DISCONNECTED', self.events)
        self.assertEqual(list(self.listener.queue), ['n1'])

    # ----- object notes at lock time -----

    def hit(self, i, text, heard=True, kind='warning'):
        return {'noteId': f'n{i}', 'kind': kind, 'heard': heard, 'spoken': text}

    def lock(self, current):
        self.listener.on_target_locked('water bottle', say=self.player.say, is_current=lambda: current[0],
                                       background=False)

    def test_lock_speaks_object_note_at_once_and_holds_guidance_until_it_ends(self):
        self.idle[0] = False  # a find task is running: idle notes would wait, object notes do not
        self.client.query_result = [self.hit(1, 'Safety note from Lan: the bottle is medicine water')]
        current = [True]
        self.lock(current)
        self.assertEqual(self.client.queries[0], ('notes:forObjectByDevice', {'deviceKey': KEY, 'label': 'water bottle'}))
        self.assertEqual(self.player.said, ['Safety note from Lan: the bottle is medicine water'])
        self.assertTrue(self.listener.holding())   # starting
        self.assertTrue(self.listener.holding())   # playing
        self.player.finish()
        self.assertFalse(self.listener.holding())  # finished: guidance resumes
        self.assertEqual(self.client.mutations, [])  # already heard earlier: no receipt

    def test_lock_with_no_notes_releases_guidance_immediately(self):
        self.client.query_result = []
        self.lock([True])
        self.assertFalse(self.listener.holding())
        self.assertEqual(self.player.said, [])

    def test_lock_speaks_only_the_most_important_note_and_marks_it_heard(self):
        self.client.query_result = [self.hit(1, 'Safety note from Lan: hot', heard=False),
                                    self.hit(2, 'Note from Lan: blue lid'), self.hit(3, 'Note from Lan: third')]
        self.lock([True])
        self.assertEqual(self.player.said, ['Safety note from Lan: hot'])  # server sorts warnings first
        self.listener.holding(); self.listener.holding()
        self.player.finish()
        self.assertFalse(self.listener.holding())
        self.listener.receipt_thread.join(timeout=2)
        self.assertEqual(self.client.mutations, [('notes:markHeardByDevice', {'deviceKey': KEY, 'noteId': 'n1'})])

    def test_stop_or_new_request_ends_the_hold(self):
        self.client.query_result = [self.hit(1, 'Note from Lan: bottle')]
        current = [True]
        self.lock(current)
        self.assertTrue(self.listener.holding())
        current[0] = False
        self.assertFalse(self.listener.holding())

    def test_slow_lookup_never_holds_guidance_longer_than_the_wait(self):
        import threading
        release, answered = threading.Event(), threading.Event()

        def slow(name, args):
            release.wait(2)
            answered.set()
            return [self.hit(1, 'Note from Lan: bottle')]

        self.client.query = slow
        self.listener.on_target_locked('bottle', say=self.player.say, is_current=lambda: True)
        self.assertTrue(self.listener.holding())
        self.clock.t = TARGET_LOOKUP_WAIT_S + .1
        self.assertFalse(self.listener.holding())
        release.set()
        answered.wait(2)
        for thread in threading.enumerate():
            if thread.name == 'family-notes-target':
                thread.join(timeout=2)
        self.assertEqual(self.player.said, [])  # a late answer is dropped: guidance already resumed

    def test_lookup_error_releases_guidance(self):
        def boom(*_a):
            raise ConnectionError('offline')
        self.client.query = boom
        self.lock([True])
        self.assertFalse(self.listener.holding())
        self.assertIn('FAMILY_NOTES_LOOKUP_FAILED', self.events)

    def test_speech_adapters_wrap_agent_priority_audio(self):
        from r3_vision.agent_audio import PriorityAudio

        class Raw(FakePlayer):
            stops = 0

            def stop(self):
                Raw.stops += 1

            def play(self, cue):
                pass

        raw = Raw()
        say, say_now, _prepare = speech_adapters(PriorityAudio(raw))
        self.assertTrue(say('Note from Lan: hi', name='FAMILY_NOTE', is_current=lambda: True))
        self.assertFalse(say('Note from Lan: no', name='FAMILY_NOTE', is_current=lambda: False))
        self.assertTrue(say_now('Safety note from Lan: now', is_current=lambda: True))
        self.assertEqual(Raw.stops, 1)
        self.assertEqual(raw.said, ['Note from Lan: hi', 'Safety note from Lan: now'])
        plain = Raw()
        say, say_now, _prepare = speech_adapters(plain)
        self.assertEqual(say, plain.say)

    # ----- Family Voice: the laptop plays the note audio rendered in the helper's own voice -----

    def voiced(self, fetch):
        self.prepared = []
        self.listener = FamilyNotesListener(self.client, KEY, say=self.player.say, speaking=self.player.speaking,
                                            is_idle=lambda: self.idle[0], clock=self.clock,
                                            log=lambda event, **kw: self.events.append(event),
                                            prepare_audio=lambda text, wav: self.prepared.append((text, wav)),
                                            fetch_audio=fetch)

    def test_idle_note_plays_its_rendered_audio_when_convex_has_it(self):
        fetched = []
        self.voiced(lambda url: fetched.append(url) or b'RIFF....WAVE-lan-voice')
        url = 'https://amiable-stoat-963.convex.cloud/api/storage/abc'
        self.listener.update({'deviceLabel': 'Laptop', 'notes': [dict(note(1, 'Note from Lan: hi'), audioUrl=url)]})
        self.listener.tick()
        self.assertEqual(self.prepared, [('Note from Lan: hi', b'RIFF....WAVE-lan-voice')])
        self.assertEqual(self.player.said, ['Note from Lan: hi'])
        self.listener.tick(); self.player.finish(); self.listener.tick()   # heard
        self.listener.update({'deviceLabel': 'Laptop', 'notes': [dict(note(1, 'Note from Lan: hi'), audioUrl=url)]})
        self.listener.tick()
        self.assertEqual(fetched, [url])  # cached: replays never download again

    def test_audio_download_failure_falls_back_to_speaking_the_text(self):
        def offline(url):
            raise OSError('offline')
        self.voiced(offline)
        self.listener.update({'deviceLabel': 'Laptop', 'notes': [dict(note(1, 'Note from Lan: hi'),
                                                                       audioUrl='https://x.convex.cloud/s')]})
        self.listener.tick()
        self.assertEqual(self.prepared, [])
        self.assertEqual(self.player.said, ['Note from Lan: hi'])
        self.assertIn('FAMILY_NOTE_AUDIO_FAILED', self.events)

    def test_only_https_audio_urls_are_used(self):
        self.voiced(lambda url: b'RIFF....WAVE')
        self.listener.update({'deviceLabel': 'Laptop', 'notes': [dict(note(1, 'Note from Lan: hi'),
                                                                       audioUrl='file:///etc/passwd')]})
        self.listener.tick()
        self.assertEqual(self.prepared, [])

    def test_object_note_at_lock_plays_its_rendered_audio(self):
        self.voiced(lambda url: b'RIFF....WAVE-bottle')
        self.client.query_result = [dict(self.hit(1, 'Safety note from Lan: medicine water'),
                                         audioUrl='https://x.convex.cloud/bottle')]
        self.lock([True])
        self.assertEqual(self.prepared, [('Safety note from Lan: medicine water', b'RIFF....WAVE-bottle')])

    def test_note_audio_provider_serves_prepared_wav_once_then_defers(self):
        from r3_vision.family_notes import NoteAudio

        class Inner:
            def synthesize(self, text):
                return b'cartesia:' + text.encode()

        provider = NoteAudio(Inner())
        provider.prepare('Note from Lan:  hi', b'RIFF-lan')
        self.assertEqual(provider.synthesize('Note from Lan: hi'), b'RIFF-lan')   # same text after cleaning
        self.assertEqual(provider.synthesize('Note from Lan: hi'), b'cartesia:Note from Lan: hi')
        with self.assertRaises(RuntimeError):
            NoteAudio(None).synthesize('anything')  # no provider: the player uses the Windows voice

    def test_adapters_install_the_note_audio_provider_once_in_front_of_the_players_own(self):
        from r3_vision.family_notes import NoteAudio
        inner = object()
        player = SimpleNamespace(say=lambda *a, **k: True, stop=lambda: None, speech_provider=inner)
        _say, _now, prepare = speech_adapters(player)
        self.assertIsInstance(player.speech_provider, NoteAudio)
        self.assertIs(player.speech_provider.inner, inner)
        speech_adapters(player)
        self.assertIs(player.speech_provider.inner, inner)  # not wrapped twice
        prepare('x', b'RIFF')
        self.assertEqual(player.speech_provider.synthesize('x'), b'RIFF')

    def test_clean_strips_control_characters_and_bounds_length(self):
        self.assertEqual(clean('a\x07b   c'), 'ab c')
        self.assertEqual(len(clean('x' * 1000)), 400)


class PipelineHookTests(unittest.TestCase):
    """live_tracking.TrackingWorker hooks, on a stub worker (no camera, no audio device)."""

    def setUp(self):
        try:
            from r3_vision.live_tracking import TrackingWorker
        except ImportError as exc:  # needs numpy/opencv like the other pipeline tests
            self.skipTest(f'pipeline dependencies missing: {exc}')
        self.Worker = TrackingWorker
        self.calls = []
        notes = SimpleNamespace(on_target_locked=lambda label, **kw: self.calls.append((label, kw)))
        self.session = SimpleNamespace(active=True, query='find the bottle', state='SEARCHING', label='',
                                       inflight=None)
        self.worker = SimpleNamespace(family_notes=notes, family_say_now=object(), family_locked=None,
                                      session=self.session, agents=None)

    def on_lock(self):
        self.Worker._family_on_lock(self.worker)

    def test_announces_once_when_the_find_task_first_locks(self):
        self.on_lock()
        self.assertEqual(self.calls, [])  # not locked yet
        self.session.state, self.session.label = 'LOCKED', 'water bottle'
        self.on_lock()
        self.on_lock()
        self.assertEqual([c[0] for c in self.calls], ['water bottle'])
        self.assertIs(self.calls[0][1]['say'], self.worker.family_say_now)
        is_current = self.calls[0][1]['is_current']
        self.assertTrue(is_current())
        self.session.state = 'REACQUIRING'   # lost and found again: same task, no repeat
        self.on_lock()
        self.session.state = 'LOCKED'
        self.on_lock()
        self.assertEqual(len(self.calls), 1)
        self.session.query = 'find my keys'   # a new request ends the old note
        self.assertFalse(is_current())

    def test_a_new_task_for_the_same_object_announces_again(self):
        self.session.state, self.session.label = 'LOCKED', 'bottle'
        self.on_lock()
        self.session.active = False           # Stop / Thanks Vision
        self.on_lock()
        self.session.active = True
        self.on_lock()
        self.assertEqual(len(self.calls), 2)

    def test_idle_means_no_task_no_request_and_no_running_agent(self):
        idle = lambda: self.Worker._family_idle(self.worker)  # noqa: E731
        self.assertFalse(idle())
        self.session.active = False
        self.assertTrue(idle())
        self.session.inflight = object()
        self.assertFalse(idle())
        self.session.inflight = None
        status = ['running']
        self.worker.agents = SimpleNamespace(coordinator=SimpleNamespace(snapshot=lambda: {'status': status[0]}))
        self.assertFalse(idle())
        status[0] = 'idle'
        self.assertTrue(idle())

    def test_live_loop_keeps_guidance_quiet_while_object_notes_play(self):
        import tempfile
        import time
        from unittest.mock import Mock, patch
        from tests.test_discovery_defaults import FreshSource
        from r3_vision.live_tracking import arguments

        player = SimpleNamespace(play=Mock(), stop=Mock(), close=Mock(), say=Mock(return_value=True),
                                 speaking=lambda: False, status='SIMULATED')
        fake = SimpleNamespace(holding=lambda: True, start=Mock(), close=Mock(), on_target_locked=Mock())
        env = {'CONVEX_URL': 'https://x.convex.cloud', 'FAMILY_NOTES_DEVICE_KEY': KEY}
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(['--video', 'SIMULATED', '--tracker', 'orb', '--detection', 'gemini',
                              '--no-hands', '--audio'])
            args.output = Path(directory)
            with patch.dict('os.environ', env), patch('r3_vision.live_tracking.Source', FreshSource), \
                    patch('r3_vision.audio_guidance.ClipPlayer', return_value=player), \
                    patch('r3_vision.family_notes.FamilyNotesListener.from_env', return_value=fake):
                worker = self.Worker(args, Mock())
                worker.audio.update = Mock()
                try:
                    deadline = time.monotonic() + 2
                    while worker.panel is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertIs(worker.family_notes, fake)
                    fake.start.assert_called_once()
                    self.assertEqual(worker.panel['direction'], (None, 'FAMILY NOTE'))
                    worker.audio.update.assert_not_called()
                finally:
                    worker.close()
                fake.close.assert_called_once()

    def test_off_without_configuration(self):
        self.assertFalse(FamilyNotesListener.configured({}))
        self.assertFalse(FamilyNotesListener.configured({'CONVEX_URL': 'https://x.convex.cloud'}))
        self.assertTrue(FamilyNotesListener.configured({'CONVEX_URL': 'https://x.convex.cloud',
                                                         'FAMILY_NOTES_DEVICE_KEY': KEY}))


if __name__ == '__main__':
    unittest.main()
