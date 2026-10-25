"""Cartesia voice for the Windows tool: SIMULATED replies, no network or credits."""
import io
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from r3_vision import audio_guidance, cartesia_voice


def streamed_wav(samples=2400, rate=24000):
    """Cartesia-style WAV whose RIFF/data sizes are streaming placeholders."""
    data = struct.pack(f'<{samples}h', *([1000, -1000] * (samples // 2)))
    fmt = struct.pack('<HHIIHH', 1, 1, rate, rate * 2, 2, 16)
    return (b'RIFF' + struct.pack('<I', 0xFFFFFFFF) + b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt
            + b'data' + struct.pack('<I', 0xFFFFFFFF) + data)


class WavTests(unittest.TestCase):
    def test_streamed_header_becomes_a_readable_wav(self):
        with wave.open(io.BytesIO(cartesia_voice.wav_bytes(streamed_wav())), 'rb') as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 24000, 2400))

    def test_non_wav_reply_is_rejected(self):
        with self.assertRaises(ValueError):
            cartesia_voice.wav_bytes(b'{"error": "nope"}')


class ConfigTests(unittest.TestCase):
    def test_all_three_values_are_required(self):
        keys = ('CARTESIA_API_KEY', 'CARTESIA_VOICE_ID', 'CARTESIA_MODEL')
        with patch.dict(os.environ, {k: '' for k in keys}), patch('r3_vision.gemini.load_dotenv'):
            self.assertIsNone(cartesia_voice.config())
        with patch.dict(os.environ, dict(zip(keys, ('k', 'v', 'sonic-3.5')))), patch('r3_vision.gemini.load_dotenv'):
            cfg = cartesia_voice.config()
            self.assertEqual(cfg['voice_id'], 'v')
            self.assertTrue(cartesia_voice.cache_name(cfg).startswith('cartesia-'))


class ClipTests(unittest.TestCase):
    def test_cartesia_clips_are_cached_and_failures_fall_back_to_windows_voice(self):
        folder = Path(tempfile.mkdtemp())
        calls, sapi = [], []

        def fake(text, cfg, timeout_s=5.):
            calls.append(text)
            if text == 'Grab.':
                raise RuntimeError('Cartesia HTTP 500')
            return cartesia_voice.wav_bytes(streamed_wav())

        def fake_run(args, **kwargs):
            sapi.append(args[-1])
            for name in ('GRAB',):
                with wave.open(str(folder / f'{name}.wav'), 'wb') as w:
                    w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000), w.writeframes(b'\x00\x10' * 800)
        events = []
        with patch.object(cartesia_voice, 'synthesize', fake), patch.object(audio_guidance.subprocess, 'run', fake_run):
            audio_guidance.make_clips(folder, phrases={'LEFT': 'Left', 'GRAB': 'Grab.'}, voice={'model': 'm'},
                                      log=lambda event, **f: events.append((event, f.get('cue'))))
            self.assertEqual(sorted(calls), ['Grab.', 'Left'])
            self.assertEqual(len(sapi), 1)
            self.assertIn("Speak('Grab.')", sapi[0])
            self.assertNotIn("Speak('Left')", sapi[0])
            self.assertIn(('VOICE_FALLBACK', 'GRAB'), events)
            audio_guidance.make_clips(folder, phrases={'LEFT': 'Left', 'GRAB': 'Grab.'}, voice={'model': 'm'})
            self.assertEqual(len(calls), 2)  # cached: no second request
        self.assertTrue((folder / 'LEFT.wav').exists() and (folder / 'GRAB.wav').exists())


if __name__ == '__main__':
    unittest.main()
