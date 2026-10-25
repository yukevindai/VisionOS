"""Always-listening voice commands for the Windows preview: offline Windows speech recognition, fixed phrases.

A PowerShell child process hosts System.Speech's recognizer with a small grammar (the six demo objects with their
aliases behind a verb, "what's around me", "stop", "stop tracking") and prints each recognition with its confidence.
A fixed grammar is far faster and more accurate than open dictation in a noisy room, and needs no network.

Echo guard: while Reflex is speaking (and briefly after), only "stop"/"be quiet" are accepted, so its own voice
("Pepsi can, right") never triggers a command, yet the user can always interrupt it. Low-confidence results are
ignored silently.
"""
from __future__ import annotations

import collections
import queue
import re
import subprocess
import threading
import time

from .demo_objects import OBJECTS

VERBS = ('find', 'find me', 'where is', 'get', 'locate', 'track', 'show me')
DESCRIBE = ("what's around me", 'whats around me', 'what is around me', 'describe the surroundings', 'describe surroundings',
            'what do you see', 'describe')
QUIET = ('stop', 'stop talking', 'be quiet', 'quiet', 'silence')
STOP_TRACKING = ('stop tracking', 'cancel', 'stop guiding')
EXTRA_OBJECTS = {'cards': ('cards', 'the cards', 'deck of cards', 'playing cards'),
                 'coke': ('coke', 'coca cola', 'cola'), 'redbull': ('red bull',), 'pepsi': ('pepsi', 'pepsi can')}


def object_phrases():
    """{spoken object phrase: object key} from the demo aliases (no category words: they are ambiguous)."""
    out = {}
    for key, o in OBJECTS.items():
        for alias in o.aliases + EXTRA_OBJECTS.get(key, ()) + (o.name.lower(),):
            alias = alias.replace('-', ' ').lower()
            if len(alias) >= 3 and alias not in ('card', 'deck'):
                out[alias] = key
    return out


WAKE = ('hey vision', 'hi vision', 'okay vision', 'ok vision', 'hey visions')
WAKE_REPLIES = ('yeah', "what's up", "i'm here", 'mm hmm what do you need', 'yes')  # Vision's own short replies
END = ('thanks', 'thank you', 'thanks vision', 'thank you vision', "that's all", 'that is all', 'bye', 'goodbye',
       'bye vision', 'never mind', 'nevermind', "that's it", 'all good', 'stop listening')


def phrases():
    objects = object_phrases()
    finds = {f'{verb} {article}{name}' for verb in VERBS for name in objects for article in ('', 'the ', 'my ')}
    return sorted(finds | set(DESCRIBE) | set(QUIET) | set(STOP_TRACKING) | set(WAKE) | set(END))


def interpret(text):
    """(command, argument) for a recognized phrase: ('find', key), ('describe', None), ('quiet', None),
    ('stop_tracking', None), ('ask', question) after "Hey Vision", ('wake', None) for "Hey Vision" alone,
    or (None, None)."""
    # Dictation adds capitals and punctuation ("Find the Pepsi can."); the command list has neither.
    text = ' '.join(re.sub(r"[^\w\s']", ' ', text.lower().replace('-', ' ')).split())
    if text in END:
        return 'end', None
    for wake in WAKE:
        if text == wake:
            return 'wake', None
        if text.startswith(wake + ' '):
            question = text[len(wake) + 1:]
            command, argument = interpret(question)  # "Hey Vision, find the Pepsi can" is still a command
            return (command, argument) if command not in (None, 'wake') else ('ask', question)
    if text in STOP_TRACKING:
        return 'stop_tracking', None
    if text in QUIET:
        return 'quiet', None
    if text in DESCRIBE:
        return 'describe', None
    objects = object_phrases()
    for verb in sorted(VERBS, key=len, reverse=True):
        if text.startswith(verb + ' '):
            rest = re.sub(r'^(the|my) ', '', text[len(verb) + 1:])
            if rest in objects:
                return 'find', objects[rest]
    return None, None


def closest_command(text, cutoff=.82):
    """Dictation sometimes mishears a command ("plying the pepsi can"); map a very close match to that command."""
    import difflib
    text = ' '.join(re.sub(r"[^\w\s']", ' ', text.lower().replace('-', ' ')).split())
    candidates = [p for p in phrases() if p not in WAKE and p not in END]
    match = difflib.get_close_matches(text, candidates, n=1, cutoff=cutoff)
    return interpret(match[0]) if match else (None, None)


SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$culture = [System.Globalization.CultureInfo]::GetCultureInfo('en-US')
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$choices = New-Object System.Speech.Recognition.Choices
foreach ($p in [System.IO.File]::ReadAllLines('{phrases}')) { if ($p) { [void]$choices.Add($p) } }
$builder = New-Object System.Speech.Recognition.GrammarBuilder($choices)
$builder.Culture = $culture
$commands = New-Object System.Speech.Recognition.Grammar($builder)
$commands.Name = 'commands'
$commands.Priority = 10
$engine.LoadGrammar($commands)
# Free dictation alongside the commands, so anything the user says can be written down on screen.
$dictation = New-Object System.Speech.Recognition.DictationGrammar
$dictation.Name = 'dictation'
$engine.LoadGrammar($dictation)
{input}
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()
while ($true) {
  $r = $engine.Recognize([TimeSpan]::FromSeconds(30))
  if ($r -ne $null) { [Console]::Out.WriteLine(('HEARD|{0:0.000}|{1}|{2}' -f $r.Confidence, $r.Grammar.Name, $r.Text)); [Console]::Out.Flush() }
  elseif ({once}) { $tries = 1 + $tries; if ($tries -ge 4) { break } }
}
"""


# System.Speech insists on a seekable stream; this wrapper reports seeking but just reads the live microphone audio
# arriving on standard input, in order (blocking until audio arrives).
STREAM_INPUT = """Add-Type -TypeDefinition @'
using System;
using System.IO;
public class LiveAudioStream : Stream {
    private readonly Stream source = Console.OpenStandardInput();
    private long position;
    public override bool CanRead { get { return true; } }
    public override bool CanSeek { get { return true; } }
    public override bool CanWrite { get { return false; } }
    public override long Length { get { return long.MaxValue; } }
    public override long Position { get { return position; } set { } }
    public override int Read(byte[] buffer, int offset, int count) {
        // The engine treats a short read as the end of the audio, so wait for the full amount (or the real end).
        int total = 0;
        while (total < count) {
            int read = source.Read(buffer, offset + total, count - total);
            if (read <= 0) { break; }
            total += read;
        }
        position += total;
        return total;
    }
    public override long Seek(long offset, SeekOrigin origin) { return position; }
    public override void Flush() { }
    public override void SetLength(long value) { throw new NotSupportedException(); }
    public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
}
'@
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$engine.SetInputToAudioStream((New-Object LiveAudioStream), $format)"""
MIC_RATE = 16000


def friendly_mic_name(name):
    """'Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\\r\\n;(Tony's AirPods))' -> readable name."""
    if '@System32' not in name and 'bthhfenum' not in name:
        return name
    owner = re.findall(r'\(([^()@]+)\)', name.replace('\r', ' ').replace('\n', ' '))
    return f"hands-free mic ({owner[-1].strip()})" if owner else 'Bluetooth hands-free mic'


def headset_microphone(sd, probe=False, seconds=.5, min_peak=30):
    """Input index of a connected Bluetooth headset microphone (AirPods first), or None.

    probe=True records `seconds` from each candidate (in memory, never saved) and returns the first that carries real
    sound: on some laptops the usual Windows paths (MME/DirectSound/WASAPI) deliver silence from AirPods while the
    Bluetooth Hands-Free (WDM-KS) path works, so Hands-Free paths are tried first.
    """
    try:
        devices = sd.query_devices()
        default_in = sd.default.device[0]
        host = devices[default_in]['hostapi'] if default_in is not None and default_in >= 0 else 0
    except Exception:
        return None
    found = sorted((0 if 'hands-free' in d['name'].lower() else 1, 0 if 'airpod' in d['name'].lower() else 1,
                    0 if d['hostapi'] == host else 1, i) for i, d in enumerate(devices)
                   if d['max_input_channels'] > 0 and re.search(r'airpod|headset|hands-free', d['name'], re.I))
    if not probe:
        same_host = [f for f in found if f[2] == 0]
        return min(same_host, key=lambda f: (f[1], f[3]))[3] if same_host else None
    import numpy as np
    for *_, index in found:
        try:
            rate = int(devices[index]['default_samplerate'])
            audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype='int16', device=index)
            sd.wait()
            if int(np.abs(audio.astype(np.int32)).max()) >= min_peak:
                return index
        except Exception:
            continue
    return None


class VoiceCommands:
    """Starts the recognizer process; recognized commands arrive on self.commands as (command, argument, text).

    mic: 'auto' records from a connected headset microphone such as AirPods (falling back to the Windows default
    microphone), 'default' uses the Windows default microphone, or a sounddevice input index/name.
    """
    def __init__(self, folder, speaking=lambda: False, min_confidence=.6, echo_gap_s=.5, wave=None, log=print,
                 mic='default', ask_confidence=.45, follow_up_s=8.):
        self.folder, self.speaking, self.min_confidence, self.echo_gap_s = folder, speaking, min_confidence, echo_gap_s
        self.log = log
        self.commands = queue.Queue()
        self.transcript = collections.deque(maxlen=6)  # recent things the user said, newest last
        self.transcript_confidence = .35
        self.ask_confidence, self.follow_up_s = ask_confidence, follow_up_s
        self.awaiting_until = 0.  # after "Hey Vision" alone, the next sentence is the question
        # Conversation mode: after a question, follow-ups need no wake phrase until it goes quiet for follow_up_s.
        self.conversation_until = 0.
        self.last_ask_at = -1e9
        self.status = 'starting voice recognition'
        self.last_heard = ''
        self.quiet_until = 0.
        self.process = None
        self.mic, self.mic_name, self.stream = None, 'Windows default microphone', None
        if wave is None and mic != 'default':
            try:
                import sounddevice as sd
                self.mic = (headset_microphone(sd, probe=True) if mic == 'auto'
                            else int(mic) if str(mic).isdigit() else mic)
                if self.mic is not None:
                    self.mic_name = friendly_mic_name(sd.query_devices(self.mic, 'input')['name'])
            except Exception as exc:
                self.mic = None
                self.log('VOICE_MIC_FALLBACK', error=type(exc).__name__)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'voice-phrases.txt').write_text('\n'.join(phrases()), encoding='utf-8')
        source = (f"$engine.SetInputToWaveFile('{str(wave).replace(chr(39), chr(39) * 2)}')" if wave
                  else STREAM_INPUT if self.mic is not None else '$engine.SetInputToDefaultAudioDevice()')
        script = (SCRIPT.replace('{phrases}', str(folder / 'voice-phrases.txt').replace("'", "''"))
                  .replace('{input}', source).replace('({once})', '($true)' if wave else '($false)'))
        (folder / 'voice-recognizer.ps1').write_text(script, encoding='utf-8')
        self.thread = threading.Thread(target=self._run, daemon=True, name='r3-voice')
        self.thread.start()

    def _start_microphone(self):
        """Record the chosen microphone and stream 16 kHz mono PCM into the recognizer's input."""
        import numpy as np
        import sounddevice as sd
        info = sd.query_devices(self.mic, 'input')
        rate = MIC_RATE
        try:
            sd.check_input_settings(device=self.mic, samplerate=MIC_RATE, channels=1, dtype='int16')
        except Exception:
            rate = int(info['default_samplerate'])
        chunks = queue.Queue(maxsize=200)

        def callback(data, frames, timing, status):
            samples = data[:, 0]
            if rate != MIC_RATE:  # simple resampling to the recognizer's 16 kHz
                positions = np.arange(int(len(samples) * MIC_RATE / rate)) * rate / MIC_RATE
                samples = np.interp(positions, np.arange(len(samples)), samples.astype(np.float32)).astype(np.int16)
            try:
                chunks.put_nowait(samples.astype('<i2').tobytes())
            except queue.Full:
                pass  # never block the audio driver

        def writer():
            while self.process is not None and self.process.poll() is None:
                try:
                    self.process.stdin.write(chunks.get(timeout=.5))
                    self.process.stdin.flush()
                except queue.Empty:
                    continue
                except OSError:
                    return
        self.stream = sd.InputStream(device=self.mic, samplerate=rate, channels=1, dtype='int16',
                                     blocksize=int(rate * .05), callback=callback)
        self.stream.start()
        threading.Thread(target=writer, daemon=True, name='r3-voice-mic').start()

    def _run(self):
        try:
            self.process = subprocess.Popen(
                ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                 str(self.folder / 'voice-recognizer.ps1')],
                stdin=subprocess.PIPE if self.mic is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if self.mic is not None:
                self._start_microphone()
            for raw in self.process.stdout:
                line = raw.decode('utf-8', 'replace').strip()
                if line == 'READY':
                    self.status = f'LISTENING on {self.mic_name} (say "Hey Vision…", "find the Pepsi can", "stop")'
                    self.log('VOICE_READY', microphone=self.mic_name)
                elif line.startswith('HEARD|'):
                    _, confidence, grammar, text = line.split('|', 3)
                    self.handle(text, float(confidence), time.monotonic(), grammar)
                elif line:
                    self.status = f'voice error: {line[:120]}'
            self.status = f'voice recognition stopped (exit {self.process.wait()})'
        except Exception as exc:
            self.status = f'voice unavailable ({type(exc).__name__})'

    def handle(self, text, confidence, now, grammar='commands'):
        command, argument = interpret(text)
        if command is None and grammar == 'dictation':
            command, argument = closest_command(text)
        right_after_wake = now < self.awaiting_until
        listening = right_after_wake or now < self.conversation_until
        if command == 'end' and not listening:
            command = None  # a stray "thanks" outside a conversation does nothing
        if command is None and listening and text.strip():
            # "Hey Vision" … then the question on its own, or a follow-up during the conversation.
            command, argument = 'ask', ' '.join(re.sub(r"[^\w\s'?]", ' ', text).split())
        threshold = self.ask_confidence if command in ('ask', 'wake', 'end') else self.min_confidence
        if not self.speaking() and now >= self.quiet_until and confidence >= self.transcript_confidence and text.strip():
            # Everything the user says is written down (Reflex's own voice is skipped while it speaks).
            self.transcript.append(dict(text=text.strip(), command=command if confidence >= threshold
                                        else None, at=now))
            self.log('VOICE_HEARD', text=text, grammar=grammar, confidence=round(confidence, 2))
        if command is None or confidence < threshold:
            self.log('VOICE_IGNORED', text=text, confidence=round(confidence, 2))
            return None
        if self.speaking():
            self.quiet_until = now + self.echo_gap_s
        # The question straight after "Hey Vision" is the user's own sentence, even if the short reply overlapped it.
        own_sentence = command == 'ask' and right_after_wake and text.strip().lower().rstrip('.?!') not in WAKE_REPLIES
        if (self.speaking() or now < self.quiet_until) and command != 'quiet' and not own_sentence:
            # Probably Reflex hearing itself; only "stop" / "be quiet" may interrupt speech.
            self.log('VOICE_IGNORED_WHILE_SPEAKING', text=text, confidence=round(confidence, 2))
            return None
        self.last_heard = f'"{text}" ({confidence:.2f})'
        self.awaiting_until = now + self.follow_up_s if command == 'wake' else 0.
        if command == 'ask':
            self.last_ask_at = now
            # Long enough for Gemini to answer; extended while the answer is being spoken (keep_listening).
            self.conversation_until = now + 30.
        elif command in ('end', 'find', 'stop_tracking'):
            self.conversation_until = 0.
        self.log('VOICE_COMMAND', command=command, argument=argument, text=text, confidence=round(confidence, 2))
        self.commands.put((command, argument, text))
        return command

    @property
    def in_conversation(self):
        return time.monotonic() < self.conversation_until

    def keep_listening(self, now, answered=False):
        """While Vision is speaking (or right after it answered), keep the follow-up window open."""
        if now < self.conversation_until:
            self.conversation_until = now + self.follow_up_s if answered else max(self.conversation_until,
                                                                                  now + self.follow_up_s)

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
