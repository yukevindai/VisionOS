"""Optional spoken hand guidance for the Windows preview, from validated tracking data only.

Every cue requires: a LOCKED target box and a fingertip from the SAME fresh frame (<=300 ms), both in that frame's
pixels. Without accepted LiDAR depth only LEFT/RIGHT/UP/DOWN are possible. FORWARD/BACKWARD/CLOSER/READY_TO_GRASP
need a measured LiDAR offset (camera-optical metres: x right, y down, z forward; delta = target - fingertip).
READY_TO_GRASP is a proximity cue, never contact confirmation. Gemini text never produces a cue.
Clips are generated once with the offline Windows speech voice and played without blocking; a new clip replaces the
current one, and invalid evidence silences playback at once.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import sys
import threading

PHRASES = {'LEFT': 'Left', 'RIGHT': 'Right', 'UP': 'Up', 'DOWN': 'Down', 'FORWARD': 'Forward', 'BACKWARD': 'Back',
           'CLOSER': 'Closer', 'READY_TO_GRASP': 'Ready to grasp', 'TRACKING_LOST': 'Tracking lost',
           'NEAR_OBJECT': 'Near the object. Check your grip.'}
DIRECTIONS = {'x': ('RIGHT', 'LEFT'), 'y': ('DOWN', 'UP'), 'z': ('FORWARD', 'BACKWARD')}
CM_RANGE = range(3, 61)  # spoken centimetre cues: "Left, 8 centimeters." (3D validated mode only)
CM_PHRASES = {f'{cue}_{cm}': f'{PHRASES[cue]}, {cm} centimeters.'
              for cue in ('LEFT', 'RIGHT', 'UP', 'DOWN', 'FORWARD', 'BACKWARD') for cm in CM_RANGE}


CM_DIRECTIONS = ('LEFT', 'RIGHT', 'UP', 'DOWN', 'FORWARD', 'BACKWARD')
# Instant scene summary (DEMO): "Pepsi can, left." clips joined into one sentence.
SUMMARY_NAMES = {'pepsi': 'Pepsi can', 'redbull': 'Red Bull can', 'coke': 'Coca-Cola can', 'cards': 'Deck of cards',
                 'white_bagel': 'White half bagel', 'brown_bagel': 'Brown bagel'}
SUMMARY_PHRASES = dict({f'SEE_{k}_{p}': f'{name}, {p}.' for k, name in SUMMARY_NAMES.items()
                        for p in ('left', 'center', 'right')},
                       **{f'FINDING_{k}': f'Finding the {name.lower()}.' for k, name in SUMMARY_NAMES.items()},
                       SEE_NOTHING='I do not recognize any demo object here.', LOOKING='Looking.',
                       DESCRIBE_FAILED='Sorry, I could not describe the view.')


def spoken(cue):
    return PHRASES.get(cue) or CM_PHRASES.get(cue) or cue
CLIPS = Path(__file__).resolve().parents[1] / '.runtime/audio-clips'


@dataclass
class Evidence:
    frame_age_s: float
    locked: bool
    box: tuple | None
    tip: tuple | None
    size: tuple
    offset: object = None


class GuidanceDecider:
    def __init__(self, max_age_s=.3, tolerance_m=.03, ready_m=.06, near_m=.15, release=.6):
        self.max_age_s, self.tolerance_m, self.ready_m, self.near_m = max_age_s, tolerance_m, ready_m, near_m
        self.release = release
        self.axis = None
        self.cm = None

    def reset(self):
        self.axis = None

    def _pick(self, components, tolerances):
        if self.axis in components and abs(components[self.axis]) > self.release * tolerances[self.axis]:
            return self.axis
        for axis in ('x', 'y', 'z'):
            if axis in components and abs(components[axis]) > tolerances[axis]:
                return axis
        return None

    def decide(self, ev: Evidence):
        """(cue or None, mode '2D'/'3D' or None, reason)."""
        if not (math.isfinite(ev.frame_age_s) and 0 <= ev.frame_age_s <= self.max_age_s):
            return self._none('frame not fresh')
        if not ev.locked or ev.box is None:
            return self._none('no locked target')
        if ev.tip is None:
            return self._none('no fingertip')
        w, h = ev.size
        tx, ty = ev.tip
        x0, y0, x1, y1 = ev.box
        if not (0 <= tx < w and 0 <= ty < h) or not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            return self._none('coordinates outside the frame')
        offset = ev.offset
        if offset is not None and getattr(offset, 'measured', False) and offset.delta_m is not None:
            dx, dy, dz = offset.delta_m
            if offset.distance_m <= self.ready_m:
                self.axis = None
                # Proximity only; never a touch or grasp confirmation.
                return 'NEAR_OBJECT', '3D', f'LiDAR distance {offset.distance_m * 100:.0f} cm'
            axis = self._pick({'x': dx, 'y': dy, 'z': dz}, dict.fromkeys('xyz', self.tolerance_m))
            if axis is None:
                self.axis = None
                return 'CLOSER', '3D', f'aligned, {offset.distance_m * 100:.0f} cm'
            self.axis = axis
            value = {'x': dx, 'y': dy, 'z': dz}[axis]
            self.cm = abs(value) * 100
            cue = DIRECTIONS[axis][0 if value > 0 else 1]
            if cue == 'FORWARD' and offset.distance_m <= self.near_m:
                cue = 'CLOSER'
            return cue, '3D', f'{axis} {value * 100:+.0f} cm, LiDAR distance {offset.distance_m * 100:.0f} cm'
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        axis = self._pick({'x': cx - tx, 'y': cy - ty}, {'x': (x1 - x0) / 2, 'y': (y1 - y0) / 2})
        if axis is None:
            self.axis = None
            return None, '2D', 'fingertip over the target in 2D; forward/closer need LiDAR depth'
        self.axis = axis
        value = cx - tx if axis == 'x' else cy - ty
        return DIRECTIONS[axis][0 if value > 0 else 1], '2D', f'{axis} {value:+.0f} px (2D only)'

    def _none(self, reason):
        self.axis = None
        return None, None, reason


class AudioGuidance:
    """Cue scheduling. Brief evidence dropouts (< grace_s) neither cut the word being spoken nor start a new one;
    longer invalid evidence stops playback. Every new cue still needs a fresh, valid frame."""

    def __init__(self, player, decider=None, repeat_s=2.5, confirm_frames=2, grace_s=.3, lost_after_s=.6,
                 lost_gap_s=4., log=None, numeric=False, cm_step=3., cm_gap_s=1.2):
        self.player = player
        # numeric: in validated 3D mode say the remaining centimetres ("Left, 8 centimeters.").
        self.numeric, self.cm_step, self.cm_gap_s = numeric, cm_step, cm_gap_s
        self.spoken_cm = None
        self.decider = decider or GuidanceDecider()
        self.repeat_s, self.confirm_frames, self.grace_s = repeat_s, confirm_frames, grace_s
        self.lost_after_s, self.lost_gap_s = lost_after_s, lost_gap_s
        self.log = log or (lambda event, **fields: None)
        self.current = None
        self.pending, self.pending_count = None, 0
        self.spoken_at = -math.inf
        self.last_valid = None
        self.invalid_since = None
        self.guided = False
        self.lost_said_at = -math.inf
        self.mode, self.reason = None, 'waiting'
        self.suppressed = None

    def reset(self):
        # Stop only a guidance cue this object started; a scene description or summary keeps playing.
        if self.current is not None or self.guided:
            self.player.stop()
        self.decider.reset()
        self.current = self.pending = None
        self.pending_count = 0
        self.guided = False
        self.invalid_since = None
        self.last_valid = None
        self.reason = 'reset'

    def _suppress(self, reason):
        if self.suppressed != reason:
            self.log('CUE_SUPPRESSED', reason=reason)
            self.suppressed = reason

    def update(self, ev: Evidence, now):
        cue, mode, reason = self.decider.decide(ev)
        self.mode, self.reason = mode, reason
        if cue is None:
            self._suppress(reason)
            if mode == '2D':
                self.last_valid, self.invalid_since = now, None
                if self.current is not None:
                    self.player.stop()
                self.current = self.pending = None
                self.pending_count = 0
            else:
                self._invalid(now)
            return None
        self.suppressed = None
        self.last_valid, self.invalid_since = now, None
        if cue != self.current and self.current is not None:
            self.pending_count = self.pending_count + 1 if cue == self.pending else 1
            self.pending = cue
            if self.pending_count < self.confirm_frames:
                return self.current
        self.pending, self.pending_count = None, 0
        cm = self.decider.cm if mode == '3D' and cue in CM_DIRECTIONS else None
        moved = (cm is not None and self.spoken_cm is not None and abs(cm - self.spoken_cm) >= self.cm_step
                 and now - self.spoken_at >= self.cm_gap_s)
        if cue != self.current or now - self.spoken_at >= self.repeat_s or moved:
            say = cue
            if self.numeric and cm is not None and round(cm) in CM_RANGE:
                say = f'{cue}_{round(cm)}'
            self.log('CUE_COMPUTED', cue=say, mode=mode, reason=reason, frameAgeMs=round(ev.frame_age_s * 1000))
            self.player.play(say)
            self.spoken_at, self.spoken_cm = now, cm
        self.current, self.guided = cue, True
        return cue

    def tick(self, now):
        if self.last_valid is not None and now - self.last_valid > self.decider.max_age_s:
            self._suppress('no fresh validated frame')
            self.reason = 'no fresh validated frame'
            self._invalid(now)
        elif self.invalid_since is not None and (self.current is not None or self.guided):
            self._invalid(now)

    def _invalid(self, now):
        if self.invalid_since is None:
            self.invalid_since = now
        if self.current is not None and now - self.invalid_since >= self.grace_s:
            self.player.stop()
            self.current = None
            self.pending, self.pending_count = None, 0
            self.last_valid = None
        if (self.guided and self.current is None and now - self.invalid_since >= self.lost_after_s
                and now - self.lost_said_at >= self.lost_gap_s):
            self.player.play('TRACKING_LOST')
            self.lost_said_at = now
            self.guided = False

    def status(self):
        if self.current is not None:
            return f'AUDIO {spoken(self.current).upper()} ({self.mode}: {self.reason})'
        return f'AUDIO silent ({self.reason})'


def trim_silence(path, threshold=400, keep_s=.02):
    """Cut the speech engine's leading/trailing silence so a cue starts as soon as it is played."""
    import wave
    import numpy as np
    with wave.open(str(path), 'rb') as source:
        params = source.getparams()
        data = source.readframes(params.nframes)
    if params.sampwidth != 2:
        return
    samples = np.frombuffer(data, '<i2').reshape(-1, params.nchannels)
    loud = np.flatnonzero(np.abs(samples).max(axis=1) > threshold)
    if not len(loud):
        return
    keep = int(keep_s * params.framerate)
    trimmed = samples[max(0, loud[0] - keep):min(len(samples), loud[-1] + keep)]
    with wave.open(str(path), 'wb') as target:
        target.setparams(params)
        target.writeframes(trimmed.tobytes())


def make_clips(folder=CLIPS, rate=3, phrases=None):
    """Offline Windows SAPI voice, generated once; returns the clip folder."""
    if sys.platform != 'win32':
        raise RuntimeError('audio clips need Windows')
    phrases = PHRASES if phrases is None else phrases
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    missing = [name for name in phrases if not (folder / f'{name}.wav').exists()]
    for start in range(0, len(missing), 50):  # batches keep the command line under the Windows limit
        lines = ['Add-Type -AssemblyName System.Speech',
                 '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer', f'$s.Rate = {int(rate)}']
        for name in missing[start:start + 50]:
            path = str(folder / f'{name}.wav').replace("'", "''")
            text = phrases[name].replace("'", "''")
            lines += [f"$s.SetOutputToWaveFile('{path}')", f"$s.Speak('{text}')"]
        lines.append('$s.Dispose()')
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', '; '.join(lines)],
                       check=True, capture_output=True, timeout=120)
        for name in missing[start:start + 50]:
            trim_silence(folder / f'{name}.wav')
    return folder


class ClipPlayer:
    """Plays the clips on its own thread so the camera loop never waits on audio.

    Uses one persistent sounddevice (PortAudio) output stream when available, so a cue starts within one audio block
    instead of waiting for a new stream to open, and an output device can be chosen by name or index; otherwise
    winsound on the Windows default device. play() only hands over the latest cue; a newer cue or stop() interrupts.
    """

    def __init__(self, folder=CLIPS, device=None, backend='auto', log=None, numeric=False):
        self.folder, self.device = Path(folder), device
        self.phrases = dict(PHRASES, **SUMMARY_PHRASES, **(CM_PHRASES if numeric else {}))
        self.log = log or (lambda event, **fields: None)
        self.backend = backend
        self.clips = {}
        self.error = ''
        self.device_name = ''
        self.ready = threading.Event()
        self.wanted = None
        self.wake = threading.Condition()
        self.closed = False
        self.stream = None
        self.buffer, self.position = None, 0
        self.buffer_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True, name='r3-audio')
        self.thread.start()

    def _prepare(self):
        import wave
        import numpy as np
        make_clips(self.folder, phrases=self.phrases)
        for name in self.phrases:
            with wave.open(str(self.folder / f'{name}.wav'), 'rb') as clip:
                rate, channels = clip.getframerate(), clip.getnchannels()
                frames = clip.readframes(clip.getnframes())
            samples = np.frombuffer(frames, '<i2').astype('float32').reshape(-1, channels) / 32768
            self.clips[name] = (samples, rate)
        if self.backend in ('auto', 'sounddevice'):
            try:
                import sounddevice as sd
                self.sd = sd
                which = self.device if self.device is not None else sd.default.device[1]
                info = sd.query_devices(which, 'output')
                rate = int(info['default_samplerate'])
                for name, (samples, clip_rate) in list(self.clips.items()):
                    mono = samples.mean(axis=1)
                    times = np.arange(int(len(mono) * rate / clip_rate)) / rate
                    self.clips[name] = (np.interp(times, np.arange(len(mono)) / clip_rate, mono).astype('float32'), rate)
                self.stream = sd.OutputStream(samplerate=rate, channels=1, dtype='float32', blocksize=int(rate * .01),
                                              device=which, callback=self._callback)
                self.stream.start()
                self.device_name = info['name']
                self.backend = 'sounddevice'
                return
            except Exception as exc:
                if self.backend == 'sounddevice':
                    raise
                self.log('PLAYBACK_FAILED', stage='sounddevice init', error=f'{type(exc).__name__}: {exc}'[:160])
        import winsound  # noqa: F401
        self.backend, self.device_name = 'winsound', 'Windows default output'

    def _run(self):
        try:
            self._prepare()
            self.log('AUDIO_INITIALIZED', backend=self.backend, device=self.device_name, clips=len(self.clips))
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'[:200]
            self.log('PLAYBACK_FAILED', stage='initialization', error=self.error)
        finally:
            self.ready.set()
        while not self.closed and not self.error:
            with self.wake:
                while self.wanted is None and not self.closed:
                    self.wake.wait()
                cue, self.wanted = self.wanted, None
            if cue == '__stop__':
                self._halt()
            elif cue is not None:
                self._play(cue)

    def _callback(self, out, frames, timing, status):
        with self.buffer_lock:
            buffer, position = self.buffer, self.position
            if buffer is None:
                out.fill(0)
                return
            chunk = buffer[position:position + frames]
            out[:len(chunk), 0] = chunk
            out[len(chunk):] = 0
            self.position = position + frames
            if self.position >= len(buffer):
                self.buffer = None

    def _playing(self):
        if self.backend != 'sounddevice':
            return None
        with self.buffer_lock:
            return self.buffer is not None

    def speaking(self, gap_s=.6):
        """True while a cue plays and for gap_s after it ends (echo guard for voice commands)."""
        import time
        return getattr(self, 'audio_active', False) or time.monotonic() - getattr(self, 'audio_end', -1e9) < gap_s

    def _play(self, cue):
        import time
        self.audio_active = True
        try:
            self._play_clip(cue)
        finally:
            self.audio_active, self.audio_end = False, time.monotonic()

    def _play_clip(self, cue):
        import time
        if cue not in self.clips:
            self.log('PLAYBACK_FAILED', cue=cue, error='no clip for this cue')
            return
        data, rate = self.clips[cue]
        duration = len(data) / rate
        started = time.monotonic()
        try:
            self.log('PLAYBACK_STARTED', cue=cue, backend=self.backend, device=self.device_name)
            if self.backend == 'sounddevice':
                with self.buffer_lock:
                    self.buffer, self.position = data, 0
            else:
                import winsound
                winsound.PlaySound(str(self.folder / f'{cue}.wav'),
                                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            while (self._playing() if self.backend == 'sounddevice' else time.monotonic() - started < duration):
                with self.wake:
                    if self.wanted is not None or self.closed:
                        self._halt()
                        self.log('PLAYBACK_INTERRUPTED', cue=cue, afterMs=round((time.monotonic() - started) * 1000))
                        return
                    self.wake.wait(min(.02, duration))
            self.log('PLAYBACK_COMPLETED', cue=cue, ms=round((time.monotonic() - started) * 1000))
        except Exception as exc:
            self.log('PLAYBACK_FAILED', cue=cue, error=f'{type(exc).__name__}: {exc}'[:200])

    def _halt(self):
        try:
            if self.backend == 'sounddevice':
                with self.buffer_lock:
                    self.buffer = None
            else:
                import winsound
                winsound.PlaySound(None, 0)
        except Exception:
            pass

    @property
    def status(self):
        if not self.ready.is_set():
            return 'preparing voice clips'
        return f'{self.backend} -> {self.device_name}' if not self.error else f'unavailable ({self.error})'

    def say(self, text, name='SPEECH'):
        """Speak any short sentence: offline Windows voice synthesised on a background thread, then played like a clip.

        The camera loop never waits. A newer say() or cue replaces it, as with every other cue."""
        import hashlib
        text = ' '.join(''.join(ch for ch in str(text) if ch.isprintable()).split())[:400]
        if not text:
            return False
        token = hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]
        request = object()
        with self.wake:
            if self.closed or getattr(self, 'muted', False) or not self.ready.is_set() or self.error:
                return False
            self.speech_token = request

        def work():
            try:
                import wave
                import numpy as np
                folder = self.folder / 'speech'
                make_clips(folder, phrases={token: text})
                with wave.open(str(folder / f'{token}.wav'), 'rb') as clip:
                    rate, channels = clip.getframerate(), clip.getnchannels()
                    samples = np.frombuffer(clip.readframes(clip.getnframes()), '<i2').astype('float32')
                samples = samples.reshape(-1, channels) / 32768
                if self.backend == 'sounddevice':
                    target = int(self.stream.samplerate)
                    mono = samples.mean(axis=1)
                    times = np.arange(int(len(mono) * target / rate)) / target
                    samples, rate = np.interp(times, np.arange(len(mono)) / rate, mono).astype('float32'), target
                with self.wake:
                    if self.closed or getattr(self, 'muted', False) or self.speech_token is not request:
                        return
                    self.clips[name] = (samples, rate)
                    if self.backend != 'sounddevice':
                        import shutil
                        shutil.copyfile(folder / f'{token}.wav', self.folder / f'{name}.wav')
                    self.wanted = name
                    self.wake.notify()
            except Exception as exc:
                self.log('PLAYBACK_FAILED', cue=name, error=f'speech synthesis {type(exc).__name__}: {exc}'[:200])
        threading.Thread(target=work, daemon=True, name='r3-speech').start()
        return True

    def play_parts(self, cues, name='SUMMARY'):
        """Join several clips (with a short pause) into one cue and play it; False if not ready."""
        import numpy as np
        if not self.ready.is_set() or self.error or not all(c in self.clips for c in cues):
            return False
        rate = self.clips[cues[0]][1]
        parts = []
        for cue in cues:
            data = self.clips[cue][0]
            parts += [data, np.zeros((int(rate * .15),) + data.shape[1:], data.dtype)]
        self.clips[name] = (np.concatenate(parts), rate)
        self.play(name)
        return True

    def play(self, cue):
        with self.wake:
            self.speech_token = None
            if self.closed or getattr(self, 'muted', False):
                return
            self.wanted = cue
            self.wake.notify()

    def stop(self):
        with self.wake:
            self.speech_token = None
            self.wanted = '__stop__' if self.ready.is_set() and not self.error else None
            self.wake.notify()

    def close(self):
        with self.wake:
            self.speech_token = None
            self.wanted = None
            self.closed = True
            self.wake.notify()
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass


def audio_test(device=None, backend='auto'):
    """Standalone check: no Gemini, YOLO, PathOn or MediaPipe."""
    import time

    def log(event, **fields):
        print(event, fields, flush=True)
    print('AUDIO_ENABLED', {'mode': 'standalone test'}, flush=True)
    try:
        import sounddevice as sd
        default_out = sd.default.device[1]
        for index, info in enumerate(sd.query_devices()):
            if info['max_output_channels'] > 0:
                print(f"  output {index}: {info['name']}{'   <- default' if index == default_out else ''}")
    except Exception as exc:
        print('  sounddevice unavailable:', type(exc).__name__)
    player = ClipPlayer(device=device, backend=backend, log=log, numeric=True)
    player.ready.wait(300)  # the first run generates the centimetre clips once (about a minute)
    for cue, pause in (('LEFT', 1.), ('RIGHT', 1.), ('UP', 1.), ('DOWN', 1.), ('LEFT_8', 2.), ('UP_3', 2.),
                       ('FORWARD_12', 2.2), ('NEAR_OBJECT', 2.6), ('TRACKING_LOST', 1.2)):
        player.play(cue)
        time.sleep(pause)
    player.close()
    print('Done. If nothing was heard, rerun with --audio-device <number or name> from the list above.')
    return 0 if not player.error else 1
