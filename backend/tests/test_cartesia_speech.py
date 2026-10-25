"""SIMULATED Cartesia HTTP and audio; no credentials or hardware used."""
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.cartesia_speech import CartesiaSpeech, SpeechError, MAX_AUDIO_BYTES, _request


class CartesiaSpeechTests(unittest.TestCase):
    def test_configuration(self):
        self.assertIsNone(CartesiaSpeech.from_env({}))
        with self.assertRaises(ValueError):
            CartesiaSpeech.from_env({'CARTESIA_API_KEY': 'secret'})
        speech = CartesiaSpeech.from_env(dict(CARTESIA_API_KEY='secret', CARTESIA_VOICE_ID='voice',
                                            CARTESIA_MODEL='model'))
        self.assertEqual((speech.voice_id, speech.model), ('voice', 'model'))

    def test_format_and_voice(self):
        calls = []
        pcm = b'\x01\x00' * 240
        def send(key, payload, timeout):
            calls.append((key, payload, timeout))
            return pcm
        result = CartesiaSpeech('secret', 'my-voice', 'my-model', transport=send).synthesize('Hello')
        with wave.open(io.BytesIO(result), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()),
                             (1, 2, 24000, 240))
            self.assertEqual(wav.readframes(240), pcm)
        key, payload, timeout = calls[0]
        self.assertEqual(payload['voice'], {'mode': 'id', 'id': 'my-voice'})
        self.assertEqual(payload['model_id'], 'my-model')
        self.assertEqual(payload['output_format']['container'], 'raw')
        self.assertNotIn(key, json.dumps(payload))
        self.assertEqual(timeout, 5)

    def test_invalid_audio_and_secret_safe_error(self):
        for value in (b'', b'1', b'12' * (MAX_AUDIO_BYTES // 2 + 1), 'not audio'):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(SpeechError, 'CARTESIA_INVALID_AUDIO'):
                    CartesiaSpeech('secret', 'voice', 'model', transport=lambda *a: value).synthesize('Hello')
        def failing(*args):
            raise OSError('secret transcript provider body')
        with self.assertRaisesRegex(SpeechError, '^CARTESIA_REQUEST_FAILED$'):
            CartesiaSpeech('secret', 'voice', 'model', transport=failing).synthesize('Hello')

    def test_deadline_and_no_unbounded_transport_threads(self):
        release, started = threading.Event(), threading.Event()
        def slow(*args):
            started.set()
            release.wait(2)
            return b'12'
        speech = CartesiaSpeech('secret', 'voice', 'model', transport=slow)
        try:
            with patch('r3_vision.cartesia_speech.TIMEOUT_S', .02):
                with self.assertRaisesRegex(SpeechError, 'CARTESIA_TIMEOUT'):
                    speech.synthesize('Hello')
                self.assertTrue(started.is_set())
                with self.assertRaisesRegex(SpeechError, 'CARTESIA_BUSY'):
                    speech.synthesize('New request')
        finally:
            release.set()

    def test_http_error_and_redirect_never_followed(self):
        for status in (302, 401, 429, 500):
            with self.subTest(status=status), patch('r3_vision.cartesia_speech.http.client.HTTPSConnection') as factory:
                conn = factory.return_value
                conn.getresponse.return_value.status = status
                with self.assertRaisesRegex(SpeechError, f'CARTESIA_HTTP_{status}'):
                    _request('secret', {}, 5)
                factory.assert_called_once_with('api.cartesia.ai', timeout=5)
                self.assertEqual(conn.request.call_count, 1)
                conn.close.assert_called_once()

    def test_input_bound(self):
        speech = CartesiaSpeech('secret', 'voice', 'model')
        for text in ('', ' ', 'a' * 401, None):
            with self.assertRaises(ValueError):
                speech.synthesize(text)


if __name__ == '__main__':
    unittest.main()
