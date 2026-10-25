"""Transcript display state only; no live microphone or visual acceptance."""
from types import SimpleNamespace
import unittest
from r3_vision.dashboard import snapshot
from r3_vision.voice_display import transcript_label, voice_view


class VoiceDisplayTests(unittest.TestCase):
    def test_empty_state_never_claims_speech_was_heard(self):
        self.assertEqual(transcript_label(None), 'You said: No speech recognized yet.')

    def test_camera_loss_retains_heard_text_and_distinct_submitted_prompt(self):
        voice = SimpleNamespace(status='LISTENING', transcript=[dict(text='Hey Vision, find a can.', command='request')],
                                last_agent_prompt='find a can.')
        state, _, _ = snapshot(None, None, 1, voice=voice)
        self.assertEqual(state['voice']['transcript'], 'Hey Vision, find a can.')
        self.assertEqual(state['voice']['submittedPrompt'], 'find a can.')
        self.assertFalse(state['fresh'])

    def test_unaccepted_text_does_not_claim_agent_submission(self):
        voice = SimpleNamespace(transcript=[dict(text='uncertain words', command=None)])
        self.assertEqual(voice_view(voice)['recognition'], 'Not accepted as a command')
        self.assertNotIn('sent to agent', transcript_label(voice))

    def test_microphone_identity_and_recent_history_survive_display_merge(self):
        voice = SimpleNamespace(input_device_name='Headset mic', status='LISTENING',
                                transcript=[dict(text='first'), dict(text='second'), dict(text='third')])
        state, _, _ = snapshot(None, None, 1, voice=voice)
        self.assertEqual(state['voice']['inputDevice'], 'Headset mic')
        self.assertEqual(state['voice']['earlier'], ['second', 'first'])
        self.assertIn('earlier: second   |   first', transcript_label(voice))
