"""Local bridge simulation: no camera, provider or audio device."""
import unittest
from r3_vision.dashboard import snapshot


class AgentDashboardTests(unittest.TestCase):
    def test_activity_remains_visible_without_fresh_camera(self):
        activity = dict(agent='scene', status='error', error='PROVIDER_TIMEOUT', events=[])
        state, _, _ = snapshot(None, None, 1, orchestration=activity)
        self.assertFalse(state['fresh'])
        self.assertEqual(state['orchestration']['error'], 'PROVIDER_TIMEOUT')
        activity['error'] = 'changed'
        self.assertEqual(state['orchestration']['error'], 'PROVIDER_TIMEOUT')
        self.assertIsNone(state['guidance'])

