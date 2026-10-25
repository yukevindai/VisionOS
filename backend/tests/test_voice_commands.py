"""Voice command parsing and echo guard; no microphone or recognizer process."""
import unittest

from r3_vision import voice_commands
from r3_vision.voice_commands import VoiceCommands, interpret, phrases


class InterpretTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(interpret('find the pepsi can'), ('find', 'pepsi'))
        self.assertEqual(interpret('where is the coke'), ('find', 'coke'))
        self.assertEqual(interpret('get my red bull'), ('find', 'redbull'))
        self.assertEqual(interpret('find the deck of cards'), ('find', 'cards'))
        self.assertEqual(interpret('find the white half bagel'), ('find', 'white_bagel'))
        self.assertEqual(interpret('find the brown bagel'), ('find', 'brown_bagel'))
        self.assertEqual(interpret('what is around me'), ('describe', None))
        self.assertEqual(interpret('stop'), ('stop_tracking', None))
        self.assertEqual(interpret('stop tracking'), ('stop_tracking', None))
        self.assertEqual(interpret('pepsi'), (None, None))  # no verb: background mention
        self.assertEqual(interpret('find the bagel'), (None, None))  # ambiguous: not in the grammar

    def test_grammar_has_every_object_behind_a_verb(self):
        grammar = set(phrases())
        for text in ('find the pepsi can', 'where is the coca cola', 'find the cards', 'stop talking'):
            self.assertIn(text, grammar)


class EchoGuardTests(unittest.TestCase):
    def voice(self, speaking):
        v = VoiceCommands.__new__(VoiceCommands)
        import queue
        v.commands, v.speaking, v.min_confidence, v.echo_gap_s = queue.Queue(), lambda: speaking, .6, .5
        v.quiet_until, v.last_heard, v.log = 0., '', lambda *a, **k: None
        import collections
        v.transcript, v.transcript_confidence = collections.deque(maxlen=6), .35
        return v

    def test_everything_said_is_written_down_but_only_commands_act(self):
        v = self.voice(False)
        self.assertIsNone(v.handle('I had a sandwich for lunch', .7, 10., 'dictation'))
        self.assertEqual(v.handle('Find the Pepsi can.', .8, 11., 'dictation'), 'find')
        self.assertEqual([(t['text'], t['command']) for t in v.transcript],
                         [('I had a sandwich for lunch', None), ('Find the Pepsi can.', 'find')])
        speaking = self.voice(True)
        speaking.handle('left', .9, 12., 'dictation')  # Reflex's own voice is not written down
        self.assertEqual(len(speaking.transcript), 0)

    def test_while_speaking_only_stop_is_accepted(self):
        v = self.voice(True)
        self.assertIsNone(v.handle('find the pepsi can', .95, 10.))
        self.assertEqual(v.handle('be quiet', .9, 10.1), 'quiet')

    def test_stop_commands_interrupt_speech_and_the_echo_gap(self):
        for text in ('stop', 'stop tracking', 'cancel', 'stop guiding'):
            for speaking in (True, False):
                with self.subTest(text=text, speaking=speaking):
                    v = self.voice(speaking)
                    v.quiet_until = 11.
                    self.assertEqual(v.handle(text, .9, 10.), 'stop_tracking')
                    self.assertEqual(v.commands.get_nowait(), ('stop_tracking', None, text))

    def test_optional_wake_and_direct_commands(self):
        v = self.voice(False)
        self.assertEqual(v.handle('Hey Vision!', .9, 10.), 'wake')
        self.assertEqual(v.commands.get_nowait()[0], 'wake')
        self.assertIsNone(v.handle('hey vision', .9, 11.))
        self.assertEqual(v.handle('find the pepsi can', .9, 12.), 'find')
        self.assertEqual(v.wake_until, 0.)
        self.assertEqual(v.handle('Hey Vision, find the Pepsi can.', .9, 13.), 'find')
        self.assertEqual(v.handle('what is around me', .9, 14.), 'describe')

    def test_timeout_does_not_disable_direct_commands(self):
        v = self.voice(False)
        v.handle('hey vision', .9, 10.)
        v.tick(16.)
        self.assertEqual(v.status, voice_commands.READY_STATUS)
        self.assertEqual(v.handle('find the pepsi can', .9, 17.), 'find')

    def test_wake_echo_confidence_and_stop(self):
        v = self.voice(True)
        self.assertIsNone(v.handle('hey vision', .9, 10.))
        self.assertIsNone(v.handle('hey vision find the pepsi can', .9, 11.))
        self.assertEqual(v.handle('Hey Vision, stop!', .9, 12.), 'stop_tracking')
        v = self.voice(False)
        self.assertIsNone(v.handle('hey vision', .4, 10.))
        self.assertTrue(v.commands.empty())
        self.assertEqual(interpret('they said hey vision'), (None, None))
        self.assertEqual(interpret('hey visionary'), (None, None))

    def test_all_original_phrases_have_optional_prefix(self):
        grammar = set(phrases())
        for phrase in grammar:
            if not phrase.startswith('hey vision'):
                self.assertIn('hey vision ' + phrase, grammar)
                self.assertEqual(interpret('hey vision ' + phrase), interpret(phrase))

    def test_low_confidence_is_ignored_and_clear_commands_pass(self):
        v = self.voice(False)
        self.assertIsNone(v.handle('find the pepsi can', .4, 10.))
        self.assertEqual(v.handle('find the pepsi can', .8, 11.), 'find')
        self.assertEqual(v.commands.get_nowait()[:2], ('find', 'pepsi'))


if __name__ == '__main__':
    unittest.main()
