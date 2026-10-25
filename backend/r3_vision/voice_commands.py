"""Always-listening voice commands for the Windows preview: offline Windows speech recognition, fixed phrases.

A PowerShell child process hosts System.Speech's recognizer with a small grammar (the six demo objects with their
aliases behind a verb, "what's around me", "stop", "stop tracking") and prints each recognition with its confidence.
A fixed grammar is far faster and more accurate than open dictation in a noisy room, and needs no network.

Echo guard: while VisionOS is speaking (and briefly after), only "stop"/"be quiet" are accepted, so its own voice
("Pepsi can, right") never triggers a command, yet the user can always interrupt it. Low-confidence results are
ignored silently.
"""
from __future__ import annotations

import queue
import re
import subprocess
import threading
import time

from .demo_objects import OBJECTS

VERBS = ('find', 'find me', 'where is', 'get', 'locate', 'track', 'show me')
DESCRIBE = ("what's around me", 'what is around me', 'describe the surroundings', 'describe surroundings',
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
    finds = {f'{verb} {article}{name}' for verb in VERBS for name in objects for article in ('', 'the ', 'my ')}
    return sorted(finds | set(DESCRIBE) | set(QUIET) | set(STOP_TRACKING))


def interpret(text):
    """(command, argument) for a recognized phrase: ('find', key), ('describe', None), ('quiet', None),
    ('stop_tracking', None), or (None, None)."""
    text = ' '.join(text.lower().replace('-', ' ').split())
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


SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$culture = [System.Globalization.CultureInfo]::GetCultureInfo('en-US')
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$choices = New-Object System.Speech.Recognition.Choices
foreach ($p in [System.IO.File]::ReadAllLines('{phrases}')) { if ($p) { [void]$choices.Add($p) } }
$builder = New-Object System.Speech.Recognition.GrammarBuilder($choices)
$builder.Culture = $culture
$engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($builder)))
{input}
[Console]::Out.WriteLine('READY'); [Console]::Out.Flush()
while ($true) {
  $r = $engine.Recognize([TimeSpan]::FromSeconds(30))
  if ($r -ne $null) { [Console]::Out.WriteLine(('HEARD|{0:0.000}|{1}' -f $r.Confidence, $r.Text)); [Console]::Out.Flush() }
  elseif ({once}) { $tries = 1 + $tries; if ($tries -ge 4) { break } }
}
"""


class VoiceCommands:
    """Starts the recognizer process; recognized commands arrive on self.commands as (command, argument, text)."""
    def __init__(self, folder, speaking=lambda: False, min_confidence=.6, echo_gap_s=.5, wave=None, log=print):
        self.folder, self.speaking, self.min_confidence, self.echo_gap_s = folder, speaking, min_confidence, echo_gap_s
        self.log = log
        self.commands = queue.Queue()
        self.status = 'starting voice recognition'
        self.last_heard = ''
        self.quiet_until = 0.
        self.process = None
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'voice-phrases.txt').write_text('\n'.join(phrases()), encoding='utf-8')
        source = (f"$engine.SetInputToWaveFile('{str(wave).replace(chr(39), chr(39) * 2)}')" if wave
                  else '$engine.SetInputToDefaultAudioDevice()')
        script = (SCRIPT.replace('{phrases}', str(folder / 'voice-phrases.txt').replace("'", "''"))
                  .replace('{input}', source).replace('({once})', '($true)' if wave else '($false)'))
        (folder / 'voice-recognizer.ps1').write_text(script, encoding='utf-8')
        self.thread = threading.Thread(target=self._run, daemon=True, name='r3-voice')
        self.thread.start()

    def _run(self):
        try:
            self.process = subprocess.Popen(
                ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                 str(self.folder / 'voice-recognizer.ps1')],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for line in self.process.stdout:
                line = line.strip()
                if line == 'READY':
                    self.status = 'LISTENING (say "find the Pepsi can", "what is around me", "stop")'
                    self.log('VOICE_READY')
                elif line.startswith('HEARD|'):
                    _, confidence, text = line.split('|', 2)
                    self.handle(text, float(confidence), time.monotonic())
                elif line:
                    self.status = f'voice error: {line[:120]}'
            self.status = f'voice recognition stopped (exit {self.process.wait()})'
        except Exception as exc:
            self.status = f'voice unavailable ({type(exc).__name__})'

    def handle(self, text, confidence, now):
        command, argument = interpret(text)
        if command is None or confidence < self.min_confidence:
            self.log('VOICE_IGNORED', text=text, confidence=round(confidence, 2))
            return None
        if self.speaking():
            self.quiet_until = now + self.echo_gap_s
        if (self.speaking() or now < self.quiet_until) and command not in ('quiet', 'stop_tracking'):
            # Stop and quiet commands always interrupt; other speech may be an echo.
            self.log('VOICE_IGNORED_WHILE_SPEAKING', text=text, confidence=round(confidence, 2))
            return None
        self.last_heard = f'"{text}" ({confidence:.2f})'
        self.log('VOICE_COMMAND', command=command, argument=argument, text=text, confidence=round(confidence, 2))
        self.commands.put((command, argument, text))
        return command

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
