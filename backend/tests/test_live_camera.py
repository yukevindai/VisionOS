"""Offline desktop-camera tests. All camera/provider data is SIMULATED."""
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from r3_vision import gemini
from r3_vision.live_camera import pixel_box, raw_point, upright, render_result, process_capture


class LiveCameraTests(unittest.TestCase):
    def test_pixel_mapping_and_edges(self):
        self.assertEqual(pixel_box(dict(x=.1, y=.2, width=.3, height=.4), (100, 200)), (10, 40, 39, 119))
        self.assertEqual(pixel_box(dict(x=0, y=0, width=1, height=1), (640, 480)), (0, 0, 639, 479))
        for region in [dict(x=-.1, y=0, width=.2, height=.1), dict(x=0, y=0, width=0, height=1),
                       dict(x=0, y=0, width=float('nan'), height=1)]:
            with self.assertRaises(ValueError):
                pixel_box(region, (100, 100))

    def test_rotation_inverse_and_dimensions(self):
        image = Image.new('RGB', (3, 2))
        image.putpixel((0, 0), (255, 0, 0))
        self.assertEqual(upright(image, 90).size, (2, 3))
        self.assertEqual(upright(image, 90).getpixel((1, 0)), (255, 0, 0))
        for rotation, point in [(0, (.2, .3)), (90, (.3, .8)), (180, (.8, .7)), (270, (.7, .2))]:
            self.assertEqual(raw_point(.2, .3, rotation), point)

    def result(self, status):
        return dict(mode='SIMULATED', timingMs={'provider': 1250}, provider={'error': None},
                    screenModel={'frameId': 'SIMULATED-1', 'coordinateSystem': 'normalizedImageTopLeft',
                                 'image': {'widthPx': 100, 'heightPx': 100}},
                    selection={'status': status, 'target': {'frameId': 'SIMULATED-1', 'label': '5',
                               'imageRegion': dict(x=.1, y=.1, width=.2, height=.2)}})

    def test_only_selected_draws_and_no_confidence_invented(self):
        image = Image.new('RGB', (100, 100), 'white')
        for status in ['NOT_FOUND', 'AMBIGUOUS', 'PROVIDER_UNAVAILABLE']:
            rendered, summary = render_result(image, self.result(status))
            self.assertEqual(rendered.tobytes(), image.tobytes())
            self.assertIn(status, summary)
        rendered, summary = render_result(image, self.result('SELECTED'))
        self.assertEqual(rendered.getpixel((10, 10)), (255, 0, 0))
        self.assertIn('confidence: not reported', summary)
        self.assertIn('1.25s', summary)
        self.assertEqual(image.getpixel((10, 10)), (255, 255, 255))

    def test_reject_mismatched_frozen_frame(self):
        result = self.result('SELECTED')
        result['selection']['target']['frameId'] = 'other'
        with self.assertRaises(ValueError):
            render_result(Image.new('RGB', (100, 100)), result)

    def test_timeout_clearly_reported_without_box(self):
        result = self.result('PROVIDER_UNAVAILABLE')
        result['provider']['error'] = {'kind': 'PROVIDER_TIMEOUT', 'status': None}
        _, summary = render_result(Image.new('RGB', (100, 100)), result)
        self.assertIn('PROVIDER_TIMEOUT', summary)

    def test_existing_pipeline_end_to_end_saves_immutable_images(self):
        fixture = Path(__file__).parent / 'fixtures/SIMULATED_gemini_reply_elevator.json'
        client = gemini.GeminiClient(api_key='replay', model='replay', live=False,
            transport=gemini.replay_transport(json.loads(fixture.read_text(encoding='utf-8'))))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'capture'
            image = Image.new('RGB', (120, 80), 'white')
            rendered, summary = process_capture(image, 90, 'Find floor 5',
                {'frameID': 'SIMULATED-1', 'snapshotUTCms': 1234}, client, directory)
            result = json.loads((directory / 'result.json').read_text())
            self.assertEqual(result['selection']['status'], 'SELECTED')
            self.assertIn('SIMULATED', summary)
            with Image.open(directory / 'original.png') as saved:
                self.assertEqual(saved.size, (120, 80))
            with Image.open(directory / 'captured.png') as saved:
                self.assertEqual(saved.size, (80, 120))
            self.assertEqual(rendered.size, (80, 120))
            self.assertEqual(result['screenModel']['capturedAtMs'], 1234)

    def test_only_recent_frames_count_as_live(self):
        from r3_vision.live_camera import frame_is_current
        self.assertFalse(frame_is_current(None, 10.0))
        self.assertTrue(frame_is_current((None, 9.5, {}), 10.0))
        self.assertTrue(frame_is_current((None, 9.0, {}), 10.0))
        self.assertFalse(frame_is_current((None, 8.9, {}), 10.0))
        self.assertFalse(frame_is_current((None, 10.5, {}), 10.0))  # clock went backwards

    def _source_after(self, video: str, seconds: float = 3.0):
        import argparse
        import time
        from r3_vision.live_camera import Source
        source = Source(argparse.Namespace(host=None, video=video, roi=None))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not source.status.startswith('Source failed'):
            time.sleep(0.05)
        source.close()
        return source

    def test_missing_camera_reports_failure_without_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source_after(str(Path(tmp) / 'no-such-stream.mp4'))
        self.assertTrue(source.status.startswith('Source failed'))
        self.assertIsNone(source.snapshot())

    def test_disconnected_stream_clears_latest_frame(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'short.avi')
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'MJPG'), 10, (64, 48))
            for _ in range(3):
                writer.write(np.zeros((48, 64, 3), np.uint8))
            writer.release()
            source = self._source_after(path)
        # End of stream behaves like a dropped connection: failure shown, no frame left to capture.
        self.assertTrue(source.status.startswith('Source failed'))
        self.assertIsNone(source.snapshot())

    def test_no_provider_request_during_import_or_preview(self):
        # Only process_capture invokes the existing pipeline, never Source or the UI timer.
        from r3_vision.live_camera import Source
        import argparse
        with patch('r3_vision.live_camera.pipeline.run') as run, patch.object(Source, 'run'):
            source = Source(argparse.Namespace(host=None, video=None, roi=(0, 0, 10, 10)))
            source.close()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
