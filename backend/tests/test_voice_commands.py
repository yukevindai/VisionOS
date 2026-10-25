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
        self.assertEqual(interpret('stop'), ('quiet', None))
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
        return v

    def test_while_speaking_only_stop_is_accepted(self):
        v = self.voice(True)
        self.assertIsNone(v.handle('find the pepsi can', .95, 10.))
        self.assertEqual(v.handle('be quiet', .9, 10.1), 'quiet')

    def test_low_confidence_is_ignored_and_clear_commands_pass(self):
        v = self.voice(False)
        self.assertIsNone(v.handle('find the pepsi can', .4, 10.))
        self.assertEqual(v.handle('find the pepsi can', .8, 11.), 'find')
        self.assertEqual(v.commands.get_nowait()[:2], ('find', 'pepsi'))


if __name__ == '__main__':
    unittest.main()
