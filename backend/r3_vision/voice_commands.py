"""Wake-aware voice commands for the Windows preview: offline Windows speech recognition, optional open dictation.

A PowerShell child process hosts System.Speech on the laptop. The normal demo
keeps its command grammar and transcribes Windows dictation. With open_ended=True,
free requests and clarification answers also reach the coordinator during an active
session. Thanks Vision or 30 seconds without an accepted command pauses routing.
The local microphone still listens for Hey Vision and Stop while paused; ordinary
speech is not retained or routed. Stop retains its explicit grammar and queue priority. Recognition
quality depends on the installed Windows language pack and microphone.

Echo guard: while VisionOS is speaking (and briefly after), only "stop"/"be quiet"/"Thanks Vision" are accepted, so its own voice
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

WAKE_PHRASE = 'hey vision'
READY_STATUS = 'LISTENING (Thanks Vision to pause; 30 s idle timeout)'
PAUSED_STATUS = 'PAUSED (local microphone listens for Hey Vision and Stop)'
SLEEP_PHRASES = ('thanks vision', 'thank you vision')
INTERRUPTS = ('quiet', 'stop_tracking', 'sleep')

VERBS = ('find', 'find me', 'where is', 'get', 'locate', 'track', 'show me',
         'help me pick up', 'help me grab', 'pick up', 'grab')
GENERIC_OBJECTS = ('can', 'bottle', 'cup', 'mug', 'bowl', 'bagel')
DESCRIBE = ("what's around me", 'whats around me', 'what is around me', 'describe the surroundings', 'describe surroundings',
            'what do you see', 'describe')
QUIET = ('stop talking', 'be quiet', 'quiet', 'silence')
STOP_TRACKING = ('stop', 'stop tracking', 'cancel', 'stop guiding')
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


def phrases():
    objects = object_phrases()
    finds = {f'{verb} {article}{name}' for verb in VERBS for name in (*objects, *GENERIC_OBJECTS)
             for article in ('', 'the ', 'my ', 'this ', 'that ', 'a ')}
    commands = finds | set(DESCRIBE) | set(QUIET) | set(STOP_TRACKING) | set(SLEEP_PHRASES)
    return sorted(commands | {WAKE_PHRASE} | {f'{WAKE_PHRASE} {p}' for p in commands})


def interpret(text):
    """(command, argument) for a recognized phrase: ('find', key), ('describe', None), ('quiet', None),
    ('stop_tracking', None), or (None, None)."""
    # Dictation adds capitals and punctuation ("Find the Pepsi can."); the command list has neither.
    text = ' '.join(re.sub(r"[^\w\s']", ' ', text.lower().replace('-', ' ')).split())
    if text == WAKE_PHRASE:
        return 'wake', None
    if text.startswith(WAKE_PHRASE + ' '):
        text = text[len(WAKE_PHRASE) + 1:]
    if text in SLEEP_PHRASES:
        return 'sleep', None
    if text in STOP_TRACKING:
        return 'stop_tracking', None
    if text in QUIET:
        return 'quiet', None
    if text in DESCRIBE:
        return 'describe', None
    objects = object_phrases()
    for verb in sorted(VERBS, key=len, reverse=True):
        if text.startswith(verb + ' '):
            rest = re.sub(r'^(the|my|this|that|a|an) ', '', text[len(verb) + 1:])
            if rest in objects:
                return 'find', objects[rest]
            if rest in GENERIC_OBJECTS:
                # A category is a query, never an assumed brand or selected target.
                # Existing visual selection must resolve duplicates/ambiguity.
                return 'find_query', f'Find the {rest}'
    return None, None


SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
# CLR callbacks must not invoke PowerShell scriptblocks on threads without a runspace.
Add-Type -ReferencedAssemblies @([System.Speech.Recognition.SpeechRecognitionEngine].Assembly.Location, 'System.dll') -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Speech.Recognition;
using System.Speech.AudioFormat;
using System.Threading;
public static class VisionVoiceHost {
    static readonly object Gate = new object();
    static readonly Stopwatch Clock = Stopwatch.StartNew();
    static long lastLevel = -1000, lastReject = -2000, lastSignal = -2000;
    static void Emit(string line) {
        lock (Gate) { Console.Out.WriteLine(line); Console.Out.Flush(); }
    }
    public static void Run(string phrases, string wave, int sampleRate) {
        using (var engine = new SpeechRecognitionEngine(CultureInfo.GetCultureInfo("en-US")))
        using (var done = new ManualResetEvent(false)) {
            var choices = new Choices();
            foreach (string p in File.ReadAllLines(phrases)) if (p.Length > 0) choices.Add(p);
            var builder = new GrammarBuilder(choices);
            builder.Culture = CultureInfo.GetCultureInfo("en-US");
            var commands = new Grammar(builder); commands.Name = "commands"; commands.Priority = 10;
            engine.LoadGrammar(commands);
            var dictation = new DictationGrammar(); dictation.Name = "dictation";
            engine.LoadGrammar(dictation);
            engine.SpeechRecognized += (s, e) => {
                Emit("HEARD|" + e.Result.Confidence.ToString("0.000", CultureInfo.InvariantCulture)
                     + "|" + e.Result.Grammar.Name + "|" + e.Result.Text.Replace('\r', ' ').Replace('\n', ' '));
            };
            engine.AudioLevelUpdated += (s, e) => {
                lock (Gate) {
                    if (Clock.ElapsedMilliseconds - lastLevel >= 1000) {
                        lastLevel = Clock.ElapsedMilliseconds; Emit("LEVEL|" + e.AudioLevel);
                    }
                }
            };
            engine.SpeechRecognitionRejected += (s, e) => {
                lock (Gate) {
                    if (Clock.ElapsedMilliseconds - lastReject >= 2000) {
                        lastReject = Clock.ElapsedMilliseconds; Emit("REJECTED");
                    }
                }
            };
            engine.AudioSignalProblemOccurred += (s, e) => {
                lock (Gate) {
                    if (Clock.ElapsedMilliseconds - lastSignal >= 2000) {
                        lastSignal = Clock.ElapsedMilliseconds; Emit("SIGNAL|" + e.AudioSignalProblem);
                    }
                }
            };
            engine.RecognizeCompleted += (s, e) => {
                if (e.Error != null) Emit("ERROR|" + e.Error.GetType().Name);
                Emit("COMPLETED"); done.Set();
            };
            if (String.IsNullOrEmpty(wave)) engine.SetInputToAudioStream(Console.OpenStandardInput(),
                new SpeechAudioFormatInfo(sampleRate, AudioBitsPerSample.Sixteen, AudioChannel.Mono));
            else engine.SetInputToWaveFile(wave);
            engine.RecognizeAsync(RecognizeMode.Multiple);
            Emit("READY");
            done.WaitOne();
        }
    }
}
'@
try { [VisionVoiceHost]::Run('{phrases}', '{wave}', {sample_rate}) }
catch { [Console]::Out.WriteLine('ERROR|' + $_.Exception.GetType().Name); [Console]::Out.Flush(); exit 1 }
"""


class VoiceCommands:
    """Starts the recognizer process; recognized commands arrive on self.commands as (command, argument, text)."""
    def __init__(self, folder, speaking=lambda: False, min_confidence=.6, echo_gap_s=.5, wave=None, log=print, open_ended=False, idle_timeout_s=30., input_device=None):
        self.folder, self.speaking, self.min_confidence, self.echo_gap_s = folder, speaking, min_confidence, echo_gap_s
        self.log = log
        self.open_ended = open_ended
        self.state_lock = threading.RLock()
        self.idle_timeout_s = float(idle_timeout_s)
        if not 0 < self.idle_timeout_s <= 300:
            raise ValueError('idle_timeout_s must be between 0 and 300 seconds')
        self.listening = True
        self.listen_until = time.monotonic() + self.idle_timeout_s
        self.commands = queue.Queue(maxsize=8)
        self.transcript = collections.deque(maxlen=6)
        self.transcript_confidence = .35
        self.status = 'starting voice recognition'
        self.last_heard = ''
        self.quiet_until = 0.
        self.wake_until = 0.
        self.process = None
        self.closed = threading.Event()
        self.process_lock = threading.Lock()
        self.microphone = None
        self.input_device = input_device
        self.input_device_name = 'Selecting Windows input' if not wave else 'WAV input (not a microphone)'
        self.wave = wave
        self.input_level = None
        self.input_status = 'waiting for microphone input'
        self._last_input_log = -float('inf')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'voice-phrases.txt').write_text('\n'.join(phrases()), encoding='utf-8')
        script = (SCRIPT.replace('{phrases}', str(folder / 'voice-phrases.txt').replace("'", "''"))
                  .replace('{wave}', str(wave).replace("'", "''") if wave else '')
                  .replace('{sample_rate}', '16000'))
        (folder / 'voice-recognizer.ps1').write_text(script, encoding='utf-8')
        self.thread = threading.Thread(target=self._run, daemon=True, name='r3-voice')
        self.thread.start()

    def _run(self):
        try:
            with self.process_lock:
                if self.closed.is_set():
                    return
                if self.wave is None:
                    from .microphone_input import PcmMicrophone
                    self.microphone = PcmMicrophone(self.input_device)
                    self.input_device_name = self.microphone.name
                    script = (SCRIPT.replace('{phrases}', str(self.folder / 'voice-phrases.txt').replace("'", "''"))
                              .replace('{wave}', '').replace('{sample_rate}', str(self.microphone.sample_rate)))
                    (self.folder / 'voice-recognizer.ps1').write_text(script, encoding='utf-8')
                    self.log('VOICE_INPUT_DEVICE', device=self.input_device_name)
                self.process = subprocess.Popen(
                    ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                     str(self.folder / 'voice-recognizer.ps1')],
                    stdin=subprocess.PIPE if self.microphone is not None else None,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for line in self.process.stdout:
                if self.closed.is_set():
                    break
                line = line.strip()
                self._recognizer_line(line, time.monotonic())
            code = self.process.wait()
            if not self.closed.is_set() and not self.status.startswith(('voice error', 'voice unavailable')):
                self.status = f'voice recognition stopped (exit {code})'
        except Exception as exc:
            if not self.closed.is_set():
                self.status = f'voice unavailable: {str(exc)[:160]}'
                self.log('VOICE_INPUT_ERROR', error=self.status)
        finally:
            # Parsing/pipe failures must not leave an unseen microphone process running.
            with self.process_lock:
                process = self.process
                if process is not None and process.poll() is None:
                    process.kill()
            if self.microphone is not None:
                self.microphone.close()
            if process is not None:
                process.wait(timeout=3)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stdin is not None:
                    process.stdin.close()

    def _microphone_error(self, error):
        self.status = f'voice unavailable: {error}'
        self.log('VOICE_INPUT_ERROR', error=str(error))
        if self.process is not None and self.process.poll() is None:
            self.process.kill()

    def _recognizer_line(self, line, now):
        """Bounded input diagnostics contain levels/categories, never rejected speech."""
        if line == 'READY':
            if self.microphone is not None:
                self.microphone.start(self.process.stdin.buffer, self._microphone_error)
            self.status = READY_STATUS if self.listening else PAUSED_STATUS
            self.log('VOICE_READY')
        elif line.startswith('HEARD|'):
            _, confidence, grammar, text = line.split('|', 3)
            self.handle(text, float(confidence), now, grammar)
        elif line.startswith('LEVEL|'):
            try:
                level = int(line.split('|', 1)[1])
            except ValueError:
                return
            if not 0 <= level <= 100:
                return
            self.input_level = level
            self.input_status = f'microphone level {level}/100' if level else 'microphone input is silent'
            if self.listening and not self.status.startswith(('voice error', 'voice unavailable')):
                self.status = READY_STATUS + ' | ' + self.input_status
            if now - self._last_input_log >= 5:
                self._last_input_log = now
                self.log('VOICE_INPUT', level=level)
        elif line == 'REJECTED':
            self.input_status = 'speech detected but not understood; try again clearly'
            if self.listening:
                self.status = self.input_status
            self.log('VOICE_REJECTED')
        elif line.startswith('SIGNAL|'):
            problem = line.split('|', 1)[1]
            if not re.fullmatch(r'[A-Za-z]+', problem):
                return
            self.input_status = f'microphone signal: {problem}'
            if self.listening:
                self.status = self.input_status
            self.log('VOICE_SIGNAL', problem=problem)
        elif line.startswith('ERROR|'):
            kind = line.split('|', 1)[1]
            self.status = f'voice error: {kind[:80]}'
            self.log('VOICE_ERROR', error=kind[:80])
        elif line == 'COMPLETED':
            self.log('VOICE_COMPLETED')
        elif line:
            self.status = f'voice error: {line[:120]}'

    def handle(self, text, confidence, now, grammar='commands'):
        from contextlib import nullcontext
        with getattr(self, 'state_lock', nullcontext()):
            return self._handle(text, confidence, now, grammar)

    def _handle(self, text, confidence, now, grammar):
        self.tick(now)
        text = ' '.join(str(text).split())[:500]
        command, argument = interpret(text)
        normalized = ' '.join(re.sub(r"[^\w\s']", ' ', text.lower().replace('-', ' ')).split())
        wakes = normalized == WAKE_PHRASE or normalized.startswith(WAKE_PHRASE + ' ')
        paused = not getattr(self, 'listening', True)
        if paused and (confidence < self.min_confidence or not (wakes or command in INTERRUPTS)):
            # Local recognizer remains awake; background speech is neither retained nor routed.
            return None
        if text and getattr(self, 'open_ended', False) and command not in (*INTERRUPTS, 'wake'):
            # Preserve the whole utterance for intent routing, including named-object
            # requests and follow-ups; only local wake/stop/sleep bypass the agent.
            command, argument = 'request', re.sub(r'^hey\s+vision\b[\s,!.:;-]*', '', text, flags=re.I) or text
        elif command is None and wakes:
            command = 'unsupported'
        if not self.speaking() and now >= self.quiet_until and confidence >= self.transcript_confidence and text:
            # Skip VisionOS playback echoes; retain the bounded laptop transcript.
            self.transcript.append(dict(text=text, command=command if confidence >= self.min_confidence
                                        else None, at=now))
            self.log('VOICE_HEARD', text=text, grammar=grammar, confidence=round(confidence, 2))
        if command is None or confidence < self.min_confidence:
            self.log('VOICE_IGNORED', text=text, confidence=round(confidence, 2))
            return None
        if self.speaking():
            self.quiet_until = now + self.echo_gap_s
        if (self.speaking() or now < self.quiet_until) and command not in INTERRUPTS:
            # Stop and quiet commands always interrupt; other speech may be an echo.
            self.log('VOICE_IGNORED_WHILE_SPEAKING', text=text, confidence=round(confidence, 2))
            return None
        if command == 'sleep':
            self._pause()
        elif command == 'wake':
            # Wake resumes the request session; direct commands work while active.
            if now < getattr(self, 'wake_until', 0.):
                return None
            self.wake_until = now + 6.
            self.status = 'Listening for your command… (Hey Vision heard)'
        else:
            self.wake_until = 0.
            self.status = PAUSED_STATUS if paused and not wakes else READY_STATUS
        if command != 'sleep' and (not paused or wakes):
            self.listening = True
            if hasattr(self, 'listen_until'):
                self.listen_until = now + getattr(self, 'idle_timeout_s', 30.)
        self.last_heard = f'"{text}" ({confidence:.2f})'
        self.log('VOICE_COMMAND', command=command, argument=argument, text=text, confidence=round(confidence, 2))
        with self.commands.mutex:
            if command in INTERRUPTS:
                self.commands.queue.clear()
            elif self.commands.maxsize and len(self.commands.queue) >= self.commands.maxsize:
                self.commands.queue.popleft()
            self.commands.queue.append((command, argument, text))
            self.commands.not_empty.notify()
        return command

    def _pause(self):
        self.listening = False
        self.wake_until = 0.
        self.listen_until = None
        if not getattr(self, 'status', '').startswith(('voice error', 'voice unavailable', 'voice recognition stopped')):
            self.status = PAUSED_STATUS
        with self.commands.mutex:
            retained = [item for item in self.commands.queue if item[0] in INTERRUPTS]
            self.commands.queue.clear()
            self.commands.queue.extend(retained)
        self.log('VOICE_PAUSED')

    def tick(self, now):
        from contextlib import nullcontext
        with getattr(self, 'state_lock', nullcontext()):
            self._tick(now)

    def _tick(self, now):
        deadline = getattr(self, 'listen_until', None)
        if getattr(self, 'listening', True) and deadline is not None and now >= deadline:
            self._pause()
        if getattr(self, 'wake_until', 0.) and now >= self.wake_until:
            self.wake_until = 0.
            # Preserve recognizer errors rather than masking them with a ready state.
            if self.status.startswith('Listening for your command'):
                self.status = READY_STATUS

    def close(self):
        self.closed.set()
        with self.process_lock:
            process = self.process
            if process is not None and process.poll() is None:
                process.kill()
        if self.microphone is not None:
            self.microphone.close()
        if process is not None:
            process.wait(timeout=3)
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=3)
