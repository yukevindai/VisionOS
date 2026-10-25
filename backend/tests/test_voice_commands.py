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
        import collections
        v.transcript, v.transcript_confidence = collections.deque(maxlen=6), .35
        v.ask_confidence, v.follow_up_s, v.awaiting_until, v.conversation_until = .45, 8., 0., 0.
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

    def test_low_confidence_is_ignored_and_clear_commands_pass(self):
        v = self.voice(False)
        self.assertIsNone(v.handle('find the pepsi can', .4, 10.))
        self.assertEqual(v.handle('find the pepsi can', .8, 11.), 'find')
        self.assertEqual(v.commands.get_nowait()[:2], ('find', 'pepsi'))


class ConversationTests(unittest.TestCase):
    def voice(self, speaking=False):
        import collections
        v = VoiceCommands.__new__(VoiceCommands)
        v.commands, v.speaking, v.min_confidence, v.echo_gap_s = queue_module.Queue(), lambda: speaking, .6, .5
        v.quiet_until, v.last_heard, v.log = 0., '', lambda *a, **k: None
        v.transcript, v.transcript_confidence = collections.deque(maxlen=6), .35
        v.ask_confidence, v.follow_up_s, v.awaiting_until, v.conversation_until = .45, 8., 0., 0.
        return v

    def drain(self, v):
        out = []
        while not v.commands.empty():
            out.append(v.commands.get_nowait()[:2])
        return out

    def test_hey_vision_questions_and_commands(self):
        self.assertEqual(interpret('Hey Vision, what colour is the cup?'), ('ask', 'what colour is the cup'))
        self.assertEqual(interpret('hey vision'), ('wake', None))
        self.assertEqual(interpret('Hey Vision find the Pepsi can'), ('find', 'pepsi'))
        self.assertEqual(interpret('thank you'), ('end', None))

    def test_wake_then_question_then_follow_ups_then_bye(self):
        v = self.voice()
        self.assertEqual(v.handle('Hey Vision', .7, 10., 'commands'), 'wake')
        self.assertEqual(v.handle('What is on my left?', .5, 12., 'dictation'), 'ask')  # no wake phrase needed
        v.keep_listening(20., answered=True)  # answer spoken
        self.assertEqual(v.handle('And what colour is it', .5, 25., 'dictation'), 'ask')  # follow-up
        self.assertEqual(v.handle('thanks', .6, 26., 'commands'), 'end')
        self.assertIsNone(v.handle('what about the cup', .6, 27., 'dictation'))  # conversation over
        self.assertEqual([c for c, _ in self.drain(v)], ['wake', 'ask', 'ask', 'end'])

    def test_question_right_after_wake_is_kept_even_while_the_reply_plays(self):
        v = self.voice(speaking=True)
        v.awaiting_until = 18.
        self.assertEqual(v.handle('Color is the cup', .6, 10.5, 'dictation'), 'ask')
        self.assertIsNone(v.handle("What's up", .9, 10.6, 'dictation'))  # Vision's own reply is not a question

    def test_misheard_dictation_maps_to_the_closest_command(self):
        self.assertEqual(voice_commands.closest_command('Plying the Pepsi can'), ('find', 'pepsi'))
        self.assertEqual(voice_commands.closest_command('I had a sandwich for lunch'), (None, None))
        v = self.voice()
        self.assertEqual(v.handle('Plying the Pepsi can', .7, 10., 'dictation'), 'find')

    def test_follow_up_window_closes_after_silence_and_stray_thanks_is_ignored(self):
        v = self.voice()
        v.handle('Hey Vision, what is that', .6, 10., 'dictation')
        v.keep_listening(15., answered=True)
        self.assertIsNone(v.handle('random chatter here', .6, 30., 'dictation'))  # 15 s after the answer
        self.assertIsNone(self.voice().handle('thanks', .9, 5., 'commands'))


class HeadsetMicrophoneTests(unittest.TestCase):
    def test_airpods_microphone_is_preferred(self):
        from types import SimpleNamespace
        devices = [{'name': 'Microphone Array (Intel)', 'max_input_channels': 2, 'hostapi': 0},
                   {'name': 'Headset (Tony’s AirPods)', 'max_input_channels': 1, 'hostapi': 0},
                   {'name': 'Headphones (Tony’s AirPods)', 'max_input_channels': 0, 'hostapi': 0},
                   {'name': 'Headset (Tony’s AirPods)', 'max_input_channels': 1, 'hostapi': 1}]
        sd = SimpleNamespace(query_devices=lambda: devices, default=SimpleNamespace(device=(0, 2)))
        self.assertEqual(voice_commands.headset_microphone(sd), 1)
        sd.query_devices = lambda: devices[:1]
        self.assertIsNone(voice_commands.headset_microphone(sd))


import queue as queue_module  # noqa: E402

if __name__ == '__main__':
    unittest.main()
