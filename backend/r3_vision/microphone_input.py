"""Explicit laptop microphone -> mono PCM pipe; no capture starts on import/construction.

The recognizer owner must terminate its child process before calling stop(), so
an outstanding pipe write can finish. This adapter never selects another device
after an error and never stores a recording or queues captured audio.
"""
from __future__ import annotations

import math
import re
import sys
import threading


class MicrophoneInputError(RuntimeError):
    """A selected input cannot supply the requested PCM stream."""


class PcmMicrophone:
    def __init__(self, device=None):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise MicrophoneInputError('Microphone capture requires sounddevice in the tracking Python environment') from exc
        self._sd = sd
        try:
            devices = list(sd.query_devices())
            self.device_index = self._select(device, devices, sd)
            info = devices[self.device_index]
            self.name = str(info['name'])
            rate = float(info['default_samplerate'])
            # Standard PCM rates supported by the Windows speech input path;
            # never silently resample or choose a different device/rate.
            rates = {8000, 11025, 12000, 16000, 22050, 24000, 32000,
                     44100, 48000, 88200, 96000, 176400, 192000}
            if not math.isfinite(rate) or not rate.is_integer() or int(rate) not in rates:
                raise MicrophoneInputError(f'Microphone {self.device_index} ({self.name}) has unsupported PCM sample rate {rate}')
            self.sample_rate = int(rate)
            if sys.byteorder != 'little':
                raise MicrophoneInputError('Windows speech PCM input requires little-endian samples')
            sd.check_input_settings(device=self.device_index, channels=1, dtype='int16', samplerate=self.sample_rate)
        except MicrophoneInputError:
            raise
        except Exception as exc:
            selected = getattr(self, 'name', repr(device))
            raise MicrophoneInputError(f'Microphone {selected}: input setup failed ({type(exc).__name__})') from exc
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stream = None
        self.thread = None
        self.error = None

    @staticmethod
    def _select(requested, devices, sd):
        if requested is None:
            index = int(sd.default.device[0])
        elif isinstance(requested, int) and not isinstance(requested, bool):
            index = requested
        elif isinstance(requested, str) and re.fullmatch(r'[+-]?\d+', requested.strip()):
            index = int(requested.strip())
        elif isinstance(requested, str) and requested.strip():
            needle = requested.strip().casefold()
            matches = [(i, item) for i, item in enumerate(devices)
                       if item.get('max_input_channels', 0) > 0 and needle in str(item['name']).casefold()]
            if not matches:
                raise MicrophoneInputError(f'No input microphone matches {requested!r}; no fallback selected')
            names = {str(item['name']).strip().casefold() for _, item in matches}
            if len(matches) > 1 and len(names) == 1:
                wasapi = [(i, item) for i, item in matches
                          if 'wasapi' in str(sd.query_hostapis(item['hostapi'])['name']).casefold()]
                if len(wasapi) == 1:
                    matches = wasapi
            if len(matches) != 1:
                options = ', '.join(f'{i}: {item["name"]}' for i, item in matches[:8])
                raise MicrophoneInputError(f'Ambiguous microphone {requested!r}; select a numeric input index ({options})')
            index = matches[0][0]
        else:
            raise MicrophoneInputError('Microphone must be an input device index or a unique nonempty name')
        if not 0 <= index < len(devices):
            raise MicrophoneInputError(f'Microphone input index {index} is unavailable; no fallback selected')
        if devices[index].get('max_input_channels', 0) < 1:
            raise MicrophoneInputError(f'Device {index} ({devices[index]["name"]}) is not an input microphone')
        return index

    def start(self, binary_pipe, on_error):
        """Start once; errors are reported once as a device-specific string."""
        if not callable(on_error) or not callable(getattr(binary_pipe, 'write', None)):
            raise TypeError('start requires a writable binary pipe and an error callback')
        with self._lock:
            if self._stop.is_set() or self.thread is not None:
                raise MicrophoneInputError('Microphone capture has already started or stopped')
            self.thread = threading.Thread(target=self._capture, args=(binary_pipe, on_error),
                                           name='r3-microphone-pcm', daemon=True)
            self.thread.start()

    def _capture(self, pipe, on_error):
        stream = None
        frames = max(1, round(self.sample_rate / 10))
        try:
            with self._lock:
                if self._stop.is_set():
                    return
                stream = self._sd.RawInputStream(device=self.device_index, samplerate=self.sample_rate,
                                                channels=1, dtype='int16', blocksize=frames)
                self._stream = stream
                stream.start()
            while not self._stop.is_set():
                data, overflowed = stream.read(frames)
                if self._stop.is_set():
                    break
                if overflowed:
                    raise MicrophoneInputError('input overflow; speech stream stopped instead of dropping audio')
                view = memoryview(data).cast('B')
                if len(view) != frames * 2:
                    raise MicrophoneInputError('incomplete mono 16-bit PCM block')
                offset = 0
                while offset < len(view) and not self._stop.is_set():
                    written = pipe.write(view[offset:])
                    if not isinstance(written, int) or not 0 < written <= len(view) - offset:
                        raise MicrophoneInputError('recognizer pipe did not accept the PCM block')
                    offset += written
                if not self._stop.is_set():
                    pipe.flush()
        except Exception as exc:
            if not self._stop.is_set():
                detail = str(exc) if isinstance(exc, MicrophoneInputError) else type(exc).__name__
                self.error = f'Microphone {self.device_index} ({self.name}): {detail}'
                self._stop.set()
                on_error(self.error)
        finally:
            with self._lock:
                if self._stream is stream:
                    self._stream = None
                if stream is not None:
                    try:
                        stream.abort()
                    except Exception:
                        pass  # An unplugged/stopped input must still be closed.
                    try:
                        stream.close()
                    except Exception:
                        pass  # Preserve the original capture failure, if any.

    def stop(self):
        """Stop capture; caller must close/kill the pipe reader first to unblock writes."""
        self._stop.set()
        with self._lock:
            stream = self._stream
            if stream is not None:
                # Interrupt a blocking read. The capture thread owns final close.
                try:
                    stream.abort()
                except Exception:
                    pass
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    close = stop
