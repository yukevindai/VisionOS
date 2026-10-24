import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from deskguide.configuration import load_config
from deskguide.engine import Engine
from deskguide.paths import ROOT
from deskguide.presentation import display_state


def result():
    return {
        'frame': SimpleNamespace(received=10., relative_lag=0.),
        'finger_uv': (1, 2),
        'metrics': dict(metric_valid=True, display_distance_m=.12,
                        display_delta_m=(.12, 0., 0.), distance_kind='LIDAR_SUPPORTED',
                        hand_depth_source='palm LiDAR + hand shape',
                        object_state='TRACKED', finger_estimated=False)
    }


class PresentationTests(unittest.TestCase):
    def test_current_metric_distance_and_directions(self):
        view = display_state(result(), 10.1)
        self.assertTrue(view.active)
        self.assertEqual(view.distance, '12.0 cm')
        self.assertIn('RIGHT 12 cm', view.directions)
        self.assertEqual(view.depth, 'LIDAR')

    def test_aged_invalid_or_future_frame_clears_live_state(self):
        for now in (10.301, 9.9, float('nan')):
            view = display_state(result(), now)
            self.assertTrue(view.stale)
            self.assertFalse(view.active)
            self.assertEqual(view.hand, 'LOST')
            self.assertEqual(view.distance, 'Unavailable')
        self.assertTrue(display_state(result(), 10.31, max_age_ms=999).stale)

    def test_estimate_is_labeled_and_cannot_display_guiding(self):
        sample = result()
        sample['metrics'].update(metric_valid=False, distance_kind='ESTIMATED',
                                 hand_depth_source='hand size/shape estimate', finger_estimated=True)
        view = display_state(sample, 10.1)
        self.assertFalse(view.active)
        self.assertTrue(view.estimated)
        self.assertEqual(view.distance, '≈ 12.0 cm')
        self.assertIn('RIGHT ≈ 12 cm', view.directions)
        self.assertEqual(view.depth, 'ESTIMATED')

    def test_predicted_target_and_lost_hand_pause(self):
        sample = result()
        sample['finger_uv'] = None
        sample['metrics'].update(metric_valid=False, object_state='PREDICTED',
                                 display_distance_m=None, display_delta_m=None)
        view = display_state(sample, 10.1)
        self.assertFalse(view.active)
        self.assertEqual(view.target, 'PREDICTED')
        self.assertEqual(view.distance, 'Unavailable')

    def test_fault_or_pause_suppresses_display_directions(self):
        for kwargs in ({'completed': True}, {'error': 'Camera disconnected'}):
            view = display_state(result(), 10.1, **kwargs)
            self.assertFalse(view.active)
            self.assertEqual(view.distance, 'Unavailable')
            self.assertNotIn('RIGHT', view.directions)

    def test_pause_stops_guidance_and_requests_generation_invalidation(self):
        config = json.loads((ROOT / 'config.json').read_text())
        guidance = Mock()
        engine = Engine(config, guidance, source=object())
        engine.pause()
        self.assertTrue(engine.completed)
        self.assertTrue(engine.reset_event.is_set())
        guidance.update.assert_called_once_with()
        engine.reset()
        self.assertFalse(engine.completed)

    def test_config_rejects_looser_or_invalid_safety_limit(self):
        config = json.loads((ROOT / 'config.json').read_text())
        args = SimpleNamespace(usb=False, host=None, port=None, target=None, rotation=None,
                               object_ms=None, hand_ms=None)
        with tempfile.TemporaryDirectory() as temp:
            args.config = Path(temp) / 'config.json'
            for limit in (301, 0, -1, float('nan')):
                config['max_frame_age_ms'] = limit
                args.config.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    load_config(args)

    def test_policy_has_no_gui_network_or_provider_imports(self):
        tree = ast.parse((ROOT / 'deskguide/guidance_core.py').read_text())
        modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        self.assertEqual(set(modules), {'math', 'dataclasses'})

    def test_hidden_window_constructs_without_camera(self):
        import tkinter as tk
        from deskguide.app import Window
        root = tk.Tk()
        root.withdraw()
        config = json.loads((ROOT / 'config.json').read_text())
        engine = Mock(result=None, error='')
        try:
            with patch('deskguide.app.tk.Tk', return_value=root), \
                 patch('deskguide.app.Engine', return_value=engine):
                window = Window(config, preview=True)
                root.update_idletasks()
                self.assertEqual(root.title(), 'Reflex | Hand guidance')
                self.assertEqual(window.distance.cget('text'), 'Unavailable')
                self.assertEqual(window.direction.cget('text'), 'STOP')
                window.close()
                engine.close.assert_called_once_with()
        finally:
            if not engine.close.called:
                root.destroy()


if __name__ == '__main__':
    unittest.main()
