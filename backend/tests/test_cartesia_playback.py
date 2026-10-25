"""SIMULATED provider/playback tests: no network, microphone or audio device."""
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from r3_vision.agent_audio import PriorityAudio
from r3_vision.audio_guidance import ClipPlayer


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(24000)
        clip.writeframes(b'\x01\x00' * 240)
    return output.getvalue()


class CartesiaPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.started, self.release = threading.Event(), threading.Event()
        self.calls, self.events, self.workers = [], [], []
        self.failure = False
        player = self.player = ClipPlayer.__new__(ClipPlayer)
        player.folder, player.backend = Path(self.folder.name), 'sounddevice'
        player.wake, player.ready = threading.Condition(), threading.Event()
        player.ready.set()
        player.closed, player.error, player.wanted = False, '', None
        player.clips = {}
        player.log = lambda event, **fields: self.events.append((event, fields))
        player.stream = SimpleNamespace(samplerate=48000, stop=lambda: None, close=lambda: None)
        player.speech_provider = SimpleNamespace(synthesize=self.synthesize)
        original_thread = threading.Thread

        def thread(*args, **kwargs):
            worker = original_thread(*args, **kwargs)
            self.workers.append(worker)
            return worker

        self.thread_patch = patch('r3_vision.audio_guidance.threading.Thread', side_effect=thread)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.addCleanup(self.finish)

    def synthesize(self, text):
        self.calls.append(text)
        self.started.set()
        if not self.release.wait(2):
            raise TimeoutError('test blocked')
        if self.failure:
            raise RuntimeError('secret provider response')
        return wav_bytes()

    def finish(self):
        self.release.set()
        for worker in self.workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def begin(self, audio=None):
        self.assertTrue((audio or self.player).say('SIMULATED response'))
        self.assertTrue(self.started.wait(2))

    def test_cloud_audio_uses_existing_resampler_and_one_worker(self):
        with patch('r3_vision.audio_guidance.make_clips') as local:
            self.begin()
            self.assertFalse(self.player.say('overlap'))
            self.finish()
            local.assert_not_called()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.player.wanted, 'SPEECH')
        samples, rate = self.player.clips['SPEECH']
        self.assertEqual((len(samples), rate), (480, 48000))
        self.assertFalse(self.player.synthesis_inflight)
        self.assertIn('SPEECH_SYNTHESIS_COMPLETED', [e for e, _ in self.events])

    def test_stop_discards_provider_result(self):
        self.begin()
        self.player.stop()
        self.finish()
        self.assertEqual(self.player.wanted, '__stop__')
        self.assertNotIn('SPEECH', self.player.clips)

    def test_guidance_preempts_pending_conversation_without_cloud_call(self):
        audio = PriorityAudio(self.player)
        self.begin(audio)
        self.assertTrue(audio.play('LEFT'))
        self.finish()
        self.assertEqual(self.player.wanted, 'LEFT')
        self.assertNotIn('SPEECH', self.player.clips)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(audio.say('lower priority'))

    def test_cancelled_provider_failure_does_not_start_local_fallback(self):
        self.failure = True
        with patch('r3_vision.audio_guidance.make_clips') as local:
            self.begin()
            self.player.stop()
            self.finish()
            local.assert_not_called()
        self.assertNotIn('secret provider response', str(self.events))

    def test_provider_failure_falls_back_through_same_player(self):
        self.failure = True

        def local(folder, phrases):
            folder.mkdir(parents=True, exist_ok=True)
            for token in phrases:
                (folder / f'{token}.wav').write_bytes(wav_bytes())

        with patch('r3_vision.audio_guidance.make_clips', side_effect=local) as fallback:
            self.begin()
            self.finish()
            fallback.assert_called_once()
        self.assertEqual(self.player.wanted, 'SPEECH')
        self.assertIn('SPEECH_FALLBACK', [e for e, _ in self.events])
        self.assertNotIn('secret provider response', str(self.events))

    def test_winsound_writes_single_replaceable_cloud_file(self):
        self.player.backend = 'winsound'
        self.begin()
        self.finish()
        self.assertEqual(list(self.player.folder.glob('*.wav')), [self.player.folder / 'SPEECH.wav'])
        self.assertFalse((self.player.folder / 'speech').exists())


if __name__ == '__main__':
    unittest.main()
