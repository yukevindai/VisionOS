"""Simulated transcripts only; does not validate microphone recognition."""
import queue
import collections
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
        voice.transcript = collections.deque(maxlen=6)
        voice.transcript_confidence = .35
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


    def test_open_dictation_preserves_bounded_transcript_and_stop_punctuation(self):
        voice = self.voice()
        for i in range(10): voice.handle(f'Question {i}.', .9, i, 'dictation')
        self.assertEqual(len(voice.transcript), 6)
        self.assertEqual(voice.transcript[-1]['command'], 'request')
        self.assertEqual(voice.handle('Stop!', .9, 11, 'dictation'), 'stop_tracking')
        self.assertEqual(voice.commands.qsize(), 1)


    def test_thanks_pauses_background_requests_and_wake_resumes(self):
        voice = self.voice()
        voice.handle('What did I miss?', .9, 10)
        self.assertEqual(voice.handle('Thanks Vision!', .9, 11), 'sleep')
        self.assertEqual(voice.commands.get_nowait()[0], 'sleep')
        count = len(voice.transcript)
        logs = []
        voice.log = lambda *args, **kwargs: logs.append((args, kwargs))
        self.assertIsNone(voice.handle('private background conversation', .99, 12))
        self.assertEqual(len(voice.transcript), count)
        self.assertEqual(logs, [])
        self.assertTrue(voice.commands.empty())
        self.assertEqual(voice.handle('Hey Vision, what did I miss?', .9, 13), 'request')
        self.assertEqual(voice.commands.get_nowait()[1], 'what did I miss?')
        self.assertTrue(voice.listening)
        self.assertEqual(voice.handle('the red one', .9, 14), 'request')

    def test_sleep_interrupts_playback_and_low_confidence_cannot_wake(self):
        voice = self.voice(True)
        self.assertEqual(voice.handle('Thank you Vision', .9, 10), 'sleep')
        self.assertFalse(voice.listening)
        self.assertIsNone(voice.handle('Hey Vision', .9, 11))  # echo guard
        voice.speaking = lambda: False
        self.assertIsNone(voice.handle('Hey Vision', .4, 12))
        self.assertFalse(voice.listening)
        self.assertEqual(voice.handle('Stop', .9, 13), 'stop_tracking')
        self.assertFalse(voice.listening)
        self.assertEqual(voice.handle('Hey Vision', .9, 14), 'wake')
        self.assertTrue(voice.listening)

    def test_idle_expiry_evicts_requests_preserves_stop_and_errors(self):
        voice = self.voice()
        voice.listen_until, voice.idle_timeout_s, voice.listening = 30., 30., True
        voice.status = 'voice error: microphone disconnected'
        voice.commands.put(('request', 'old', 'old'))
        voice.commands.put(('stop_tracking', None, 'stop'))
        voice.tick(30.)
        self.assertFalse(voice.listening)
        self.assertEqual(voice.status, 'voice error: microphone disconnected')
        self.assertEqual(voice.commands.get_nowait()[0], 'stop_tracking')
        self.assertTrue(voice.commands.empty())
        self.assertIsNone(voice.handle('Describe surroundings', .9, 31.))

    def test_accepted_followup_renews_idle_deadline(self):
        voice = self.voice()
        voice.listen_until, voice.idle_timeout_s, voice.listening = 30., 30., True
        voice.handle('What did I miss?', .9, 20.)
        self.assertEqual(voice.listen_until, 50.)
        voice.tick(49.)
        self.assertTrue(voice.listening)
        voice.tick(50.)
        self.assertFalse(voice.listening)
