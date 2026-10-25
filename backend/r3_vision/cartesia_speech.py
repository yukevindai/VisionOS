"""Direct Cartesia synthesis only; the existing local scheduler owns all playback."""
from __future__ import annotations

import http.client
import io
import json
import os
import threading
import time
import wave

MAX_AUDIO_BYTES = 4 * 1024 * 1024
TIMEOUT_S = 5.0
SAMPLE_RATE = 24000


class SpeechError(RuntimeError):
    """A safe diagnostic code, never provider response bodies or credentials."""


def _request(key, payload, timeout):
    # http.client does not follow redirects (credentials never leave this host).
    connection = http.client.HTTPSConnection('api.cartesia.ai', timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        connection.request('POST', '/tts/bytes', body=json.dumps(payload), headers={
            'Authorization': f'Bearer {key}', 'Cartesia-Version': '2026-03-01',
            'Content-Type': 'application/json',
        })
        response = connection.getresponse()
        if response.status != 200:
            raise SpeechError(f'CARTESIA_HTTP_{response.status}')
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SpeechError('CARTESIA_TIMEOUT')
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_AUDIO_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                raise SpeechError('CARTESIA_AUDIO_TOO_LARGE')
        return b''.join(chunks)
    finally:
        connection.close()


class CartesiaSpeech:
    def __init__(self, api_key, voice_id, model, *, transport=None):
        self._key, self.voice_id, self.model = api_key, voice_id, model
        self._transport = transport or _request
        self._slot = threading.Lock()

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        key = env.get('CARTESIA_API_KEY', '').strip()
        if not key:
            return None
        voice = env.get('CARTESIA_VOICE_ID', '').strip()
        model = env.get('CARTESIA_MODEL', '').strip()
        if not voice or not model:
            raise ValueError('CARTESIA_VOICE_ID and CARTESIA_MODEL are required with CARTESIA_API_KEY')
        return cls(key, voice, model)

    def synthesize(self, text):
        """Return a complete mono PCM WAV within five seconds, or a safe error.

        At most one transport job exists. A timed-out transport retains its slot
        until it exits, even if DNS or a broken socket ignores its timeout.
        No transport thread can enqueue playback or publish an obsolete result.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > 400:
            raise ValueError('Cartesia speech requires 1 to 400 characters')
        if not self._slot.acquire(blocking=False):
            raise SpeechError('CARTESIA_BUSY')
        done, result = threading.Event(), []
        payload = dict(model_id=self.model, transcript=text,
                       voice=dict(mode='id', id=self.voice_id), language='en',
                       output_format=dict(container='raw', encoding='pcm_s16le', sample_rate=SAMPLE_RATE))

        def work():
            try:
                pcm = self._transport(self._key, payload, TIMEOUT_S)
                if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_BYTES:
                    raise SpeechError('CARTESIA_INVALID_AUDIO')
                target = io.BytesIO()
                with wave.open(target, 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(SAMPLE_RATE)
                    wav.writeframes(pcm)
                result.append(target.getvalue())
            except SpeechError as exc:
                result.append(exc)
            except Exception:
                result.append(SpeechError('CARTESIA_REQUEST_FAILED'))
            finally:
                self._slot.release()
                done.set()

        threading.Thread(target=work, daemon=True, name='cartesia-transport').start()
        if not done.wait(TIMEOUT_S):
            raise SpeechError('CARTESIA_TIMEOUT')
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]
