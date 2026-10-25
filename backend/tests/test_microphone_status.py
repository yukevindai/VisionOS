"""Capture diagnostics and lifecycle; opt-in Windows test uses synthetic WAV, not a microphone."""
import collections
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from r3_vision.voice_commands import VoiceCommands, READY_STATUS, PAUSED_STATUS


class MicrophoneStatusTests(unittest.TestCase):
    def voice(self):
        voice = VoiceCommands.__new__(VoiceCommands)
        voice.listening = True
        voice.status = READY_STATUS
        voice.log = Mock()
        voice.handle = Mock()
        voice._last_input_log = -float('inf')
        voice.transcript = collections.deque(maxlen=6)
        return voice

    def test_levels_visible_but_logs_bounded_and_invalid_values_ignored(self):
        voice = self.voice()
        voice._recognizer_line('LEVEL|23', 10.)
        self.assertEqual(voice.input_level, 23)
        self.assertIn('23/100', voice.status)
        voice._recognizer_line('LEVEL|0', 11.)
        self.assertIn('silent', voice.input_status)
        voice._recognizer_line('LEVEL|101', 12.)
        voice._recognizer_line('LEVEL|invalid', 12.)
        self.assertEqual(voice.input_level, 0)
        self.assertEqual(voice.log.call_count, 1)
        voice._recognizer_line('LEVEL|8', 16.)
        self.assertEqual(voice.log.call_count, 2)

    def test_paused_diagnostics_do_not_create_transcripts_or_resume_routing(self):
        voice = self.voice()
        voice.listening = False
        voice.status = PAUSED_STATUS
        for line in ['LEVEL|20', 'REJECTED', 'SIGNAL|TooNoisy']:
            voice._recognizer_line(line, 10.)
        self.assertEqual(voice.status, PAUSED_STATUS)
        self.assertFalse(voice.listening)
        self.assertEqual(list(voice.transcript), [])
        voice.handle.assert_not_called()
        self.assertNotIn('text', str(voice.log.call_args_list))

    def test_recognized_text_uses_existing_confidence_and_session_gate(self):
        voice = self.voice()
        voice._recognizer_line('HEARD|0.870|dictation|Help me pick up this can.', 10.)
        voice.handle.assert_called_once_with('Help me pick up this can.', .87, 10., 'dictation')
        self.assertEqual(list(voice.transcript), [])

    def test_rejection_and_engine_errors_are_visible(self):
        voice = self.voice()
        voice._recognizer_line('REJECTED', 10.)
        self.assertIn('not understood', voice.status)
        voice._recognizer_line('ERROR|InvalidOperationException', 11.)
        voice._recognizer_line('LEVEL|20', 12.)
        self.assertEqual(voice.status, 'voice error: InvalidOperationException')

    def test_close_before_worker_start_never_opens_recognizer(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('r3_vision.voice_commands.threading.Thread') as thread, \
                patch('r3_vision.voice_commands.subprocess.Popen') as popen:
            voice = VoiceCommands(Path(directory), log=Mock())
            voice.close()
            voice._run()
            popen.assert_not_called()
            self.assertTrue(voice.closed.is_set())
            thread.return_value.join.assert_called_once()

    def test_pipe_parse_failure_kills_and_reaps_recognizer(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('r3_vision.voice_commands.threading.Thread'), \
                patch('r3_vision.voice_commands.subprocess.Popen') as popen:
            voice = VoiceCommands(Path(directory), wave=Path(directory) / 'synthetic.wav', log=Mock())
            process = popen.return_value
            process.stdout = Mock()
            process.stdout.__iter__ = Mock(return_value=iter(['HEARD|malformed\n']))
            process.poll.return_value = None
            voice._run()
            process.kill.assert_called_once()
            process.wait.assert_called_once_with(timeout=3)
            process.stdout.close.assert_called_once()
            self.assertIn('voice unavailable', voice.status)


    def test_selected_pcm_input_keeps_async_diagnostics_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('r3_vision.voice_commands.threading.Thread'), \
                patch('r3_vision.voice_commands.subprocess.Popen') as popen, \
                patch('r3_vision.microphone_input.PcmMicrophone') as microphone:
            microphone.return_value.name = 'Selected headset'
            microphone.return_value.sample_rate = 48000
            voice = VoiceCommands(Path(directory), input_device='Selected headset', log=Mock())
            process = popen.return_value
            process.stdout = Mock()
            process.stdout.__iter__ = Mock(return_value=iter(['READY\n', 'LEVEL|23\n']))
            process.poll.return_value = 0
            voice._run()
            microphone.assert_called_once_with('Selected headset')
            microphone.return_value.start.assert_called_once_with(process.stdin.buffer, voice._microphone_error)
            microphone.return_value.close.assert_called_once()
            process.stdin.close.assert_called_once()
            self.assertEqual(voice.input_level, 23)
            self.assertEqual(voice.input_device_name, 'Selected headset')
            script = (Path(directory) / 'voice-recognizer.ps1').read_text()
            self.assertIn('SetInputToAudioStream(Console.OpenStandardInput()', script)
            self.assertIn("'', 48000)", script)
            self.assertNotIn('SetInputToDefaultAudioDevice', script)
            self.assertIn('AudioLevelUpdated', script)


@unittest.skipUnless(os.name == 'nt' and os.getenv('R3_TEST_WINDOWS_SPEECH') == '1',
                     'opt-in installed Windows speech engine; synthetic audio only')
class WindowsSyntheticRecognitionTests(unittest.TestCase):
    def test_real_windows_engine_transcribes_synthetic_wave_and_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            script = folder / 'synthesize.ps1'
            wave = folder / 'synthetic.wav'
            script.write_text("param([string]$WavePath)\nAdd-Type -AssemblyName System.Speech\n"
                              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                              "try { $s.SetOutputToWaveFile($WavePath); $s.Speak('Hey Vision. Stop.') }\n"
                              "finally { $s.Dispose() }\n", encoding='utf-8')
            subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                            '-File', str(script), '-WavePath', str(wave)], check=True, timeout=15,
                           capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            events = []
            voice = VoiceCommands(folder, wave=wave, log=lambda event, **data: events.append((event, data)))
            try:
                voice.thread.join(timeout=20)
                self.assertFalse(voice.thread.is_alive(), voice.status)
                heard = [data['text'].lower() for event, data in events if event == 'VOICE_HEARD']
                self.assertIn('hey vision', heard, events)
                self.assertIn('stop', heard, events)
                self.assertEqual(list(voice.commands.queue)[-1][0], 'stop_tracking')
                self.assertEqual(voice.process.returncode, 0)
            finally:
                voice.close()


if __name__ == '__main__':
    unittest.main()
