"""Cartesia text-to-speech for the Windows live tool: same request as backend/convex/lib/cartesia.ts.

Reads CARTESIA_API_KEY, CARTESIA_VOICE_ID and CARTESIA_MODEL from the environment or backend/.env (never logged).
With all three set, Reflex speaks with the Cartesia voice: fixed cues are generated once and cached as clips (instant,
offline afterwards); longer lines (Describe surroundings) are synthesized live. Any failure or missing setting falls
back to the offline Windows voice, so speech never goes silent. About one Cartesia credit per character.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import urllib.error
import urllib.request
import wave
from pathlib import Path

URL = 'https://api.cartesia.ai/tts/bytes'
VERSION = '2026-03-01'
SAMPLE_RATE = 24_000
MAX_TEXT = 300
MAX_AUDIO_BYTES = 4 * 1024 * 1024
BACKEND = Path(__file__).resolve().parents[1]


def config():
    """dict(api_key, voice_id, model) when all three are set; otherwise None (Windows voice)."""
    from .gemini import load_dotenv
    load_dotenv(BACKEND / '.env')
    values = {name: os.environ.get(f'CARTESIA_{name.upper()}', '').strip() for name in ('api_key', 'voice_id', 'model')}
    return values if all(values.values()) else None


def cache_name(cfg):
    """Clip folder name per voice and model, so cached clips never mix voices."""
    return 'cartesia-' + hashlib.sha256(f"{cfg['voice_id']}|{cfg['model']}".encode()).hexdigest()[:10]


def wav_bytes(raw):
    """A plain PCM WAV from Cartesia's reply (its streamed header may carry placeholder sizes)."""
    if raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('reply is not a WAV file')
    channels, rate, bits = 1, SAMPLE_RATE, 16
    position = 12
    while position + 8 <= len(raw):
        chunk, size = raw[position:position + 4], struct.unpack('<I', raw[position + 4:position + 8])[0]
        body = position + 8
        if chunk == b'fmt ':
            _, channels, rate, _, _, bits = struct.unpack('<HHIIHH', raw[body:body + 16])
        elif chunk == b'data':
            data = raw[body:] if size in (0, 0xFFFFFFFF) or body + size > len(raw) else raw[body:body + size]
            if bits != 16 or not data:
                raise ValueError('unexpected audio format')
            out = io.BytesIO()
            with wave.open(out, 'wb') as w:
                w.setnchannels(channels)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(data[:len(data) - len(data) % (2 * channels)])
            return out.getvalue()
        position = body + size + (size & 1)
    raise ValueError('no audio data in reply')


def synthesize(text, cfg, timeout_s=5.):
    """WAV bytes for `text` in the configured Cartesia voice. Raises on any failure (caller falls back)."""
    text = ' '.join(''.join(ch for ch in str(text) if ch.isprintable()).split())[:MAX_TEXT]
    if not text:
        raise ValueError('empty text')
    body = json.dumps({
        'model_id': cfg['model'], 'transcript': text, 'voice': {'mode': 'id', 'id': cfg['voice_id']}, 'language': 'en',
        'output_format': {'container': 'wav', 'encoding': 'pcm_s16le', 'sample_rate': SAMPLE_RATE},
    }).encode('utf-8')
    request = urllib.request.Request(URL, data=body, method='POST', headers={
        'Authorization': f"Bearer {cfg['api_key']}", 'Cartesia-Version': VERSION, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read(MAX_AUDIO_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'Cartesia HTTP {error.code}') from None  # never echo the key or headers
    if len(raw) > MAX_AUDIO_BYTES:
        raise ValueError('reply too large')
    return wav_bytes(raw)
