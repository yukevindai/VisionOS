"""Family notes listener on the laptop. SIMULATED: fake Convex client, fake player, fake clock."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from r3_vision.family_notes import START_TIMEOUT_S, FamilyNotesListener, clean  # noqa: E402

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

    def test_clean_strips_control_characters_and_bounds_length(self):
        self.assertEqual(clean('a\x07b   c'), 'ab c')
        self.assertEqual(len(clean('x' * 1000)), 400)


if __name__ == '__main__':
    unittest.main()
