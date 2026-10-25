"""Simulated transcripts only; does not validate microphone recognition."""
import queue
import unittest
from r3_vision.voice_commands import VoiceCommands, SCRIPT


class OpenVoiceTests(unittest.TestCase):
    def voice(self, speaking=False):
        voice = VoiceCommands.__new__(VoiceCommands)
        voice.commands = queue.Queue(maxsize=2)
        voice.speaking = lambda: speaking
        voice.min_confidence, voice.echo_gap_s = .6, .5
        voice.quiet_until, voice.last_heard = 0., ''
        voice.log = lambda *args, **kwargs: None
        voice.open_ended = True
        return voice

    def test_open_request_and_clarification(self):
        voice = self.voice()
        self.assertEqual(voice.handle('What did I miss?', .9, 10), 'request')
        self.assertEqual(voice.handle('The red one beside the cup', .9, 11), 'request')
        self.assertEqual(voice.commands.qsize(), 2)

    def test_stop_evicts_requests_even_during_playback(self):
        voice = self.voice(True)
        voice.commands.put(('request', 'old', 'old'))
        self.assertIsNone(voice.handle('Describe the table', .9, 10))
        self.assertEqual(voice.handle('stop', .9, 11), 'stop_tracking')
        self.assertEqual(voice.commands.get_nowait(), ('stop_tracking', None, 'stop'))
        self.assertTrue(voice.commands.empty())

    def test_bounded_latest_requests(self):
        voice = self.voice()
        for i in range(20): voice.handle(f'Question {i}', .9, i)
        self.assertEqual(voice.commands.qsize(), 2)
        self.assertEqual(voice.commands.get_nowait()[1], 'Question 18')

