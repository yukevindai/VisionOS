"""SIMULATED blocking scene workers; no camera, provider, speech device or network."""
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from r3_vision.live_tracking import TrackingWorker
from r3_vision.tracking_session import RequestBudget


class SceneLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.worker = TrackingWorker.__new__(TrackingWorker)
        w = self.worker
        w.scene_requests = queue.Queue(maxsize=1)
        w.scene_lock = threading.RLock()
        w.scene_generation = 0
        w.scene_inflight = False
        w.scene_note = ''
        w.stopped = threading.Event()
        w.source = Mock()
        w.source.snapshot.side_effect = lambda: (Image.new('RGB', (64, 48)), time.monotonic(), {})
        w.audio = SimpleNamespace(player=Mock())
        w.session = SimpleNamespace(budget=RequestBudget(1., 10, 10))
        w.args = SimpleNamespace(rotation=0, output=Path(self.directory.name))
        w.client = object()
        w.stream_id = 'SIMULATED-scene'
        w.thread = Mock()
        w.log = None
        self.started, self.release = threading.Event(), threading.Event()
        self.threads = []
        real_thread = threading.Thread

        def thread_factory(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            self.threads.append(thread)
            return thread

        self.thread_patch = patch('r3_vision.live_tracking.threading.Thread', side_effect=thread_factory)
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.addCleanup(self.finish)

    def finish(self):
        self.release.set()
        for thread in self.threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def provider(self, *args):
        self.started.set()
        if not self.release.wait(2):
            raise AssertionError('test did not release provider')
        return dict(ok=True, summary='A cup on the left.', ms=10.)

    def start(self):
        self.worker.describe_surroundings()
        self.worker._start_scene(self.worker.scene_requests.get_nowait())
        self.assertTrue(self.started.wait(1))

    def test_duplicate_presses_coalesce_before_and_during_provider_request(self):
        w = self.worker
        with patch('r3_vision.scene_describe.describe', side_effect=self.provider) as provider:
            for _ in range(5):
                w.describe_surroundings()
            self.assertEqual(w.scene_requests.qsize(), 1)
            w._start_scene(w.scene_requests.get_nowait())
            self.assertTrue(self.started.wait(1))
            for _ in range(5):
                w.describe_surroundings()
            self.assertTrue(w.scene_requests.empty())
            self.assertEqual(w.session.budget.total, 1)
            self.finish()
            self.assertEqual(provider.call_count, 1)
            w.audio.player.say.assert_called_once_with('A cup on the left.')
            self.assertFalse(w.scene_inflight)

    def test_stop_discards_late_reply_but_keeps_slot_until_provider_finishes(self):
        w = self.worker
        with patch('r3_vision.scene_describe.describe', side_effect=self.provider):
            self.start()
            w.cancel_scene_speech()
            w.describe_surroundings()
            self.assertTrue(w.scene_requests.empty())
            self.assertTrue(w.scene_inflight)
            self.finish()
            w.audio.player.say.assert_not_called()
            self.assertEqual(w.scene_note, '')
            self.assertFalse(w.scene_inflight)
            w.describe_surroundings()
            self.assertEqual(w.scene_requests.qsize(), 1)

    def test_cancelled_queued_request_never_uses_provider_budget(self):
        w = self.worker
        w.describe_surroundings()
        queued = w.scene_requests.get_nowait()
        w.cancel_scene_speech()
        w._start_scene(queued)
        self.assertEqual(w.session.budget.total, 0)
        self.assertEqual(self.threads, [])

    def test_close_rejects_late_reply_and_new_requests(self):
        w = self.worker
        with patch('r3_vision.scene_describe.describe', side_effect=self.provider):
            self.start()
            w.close()
            w.describe_surroundings()
            self.finish()
            self.assertTrue(w.scene_requests.empty())
            w.audio.player.say.assert_not_called()
            w.audio.player.close.assert_called_once()

    def test_late_demo_summary_is_also_discarded_after_stop(self):
        w = self.worker
        w.hybrid_note = ''

        def demo(*args):
            self.provider()
            return [('pepsi', 'left')], {'total': 10.}

        w.demo = SimpleNamespace(describe=demo)
        w.describe()
        self.assertTrue(self.started.wait(1))
        w.cancel_scene_speech()
        self.finish()
        w.audio.player.play_parts.assert_not_called()
        self.assertEqual(w.hybrid_note, '')
        self.assertFalse(w.scene_inflight)


if __name__ == '__main__':
    unittest.main()
