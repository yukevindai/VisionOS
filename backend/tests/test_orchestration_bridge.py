"""SIMULATED integration with existing tracker interface; no camera/hardware."""
import copy
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.orchestration import Route
from r3_vision.orchestration_bridge import OrchestrationBridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.player = Mock()
        self.session = SimpleNamespace(generation=0, epoch=('SIMULATED', 1), inflight=None,
            tracker=None, state='SEARCHING', label='', needs_clarification=False)
        def find(query):
            self.session.generation += 1
            self.session.query = query
            self.session.tracker = None
            self.session.state = 'SEARCHING'
        self.session.find = find
        self.worker = SimpleNamespace(audio=SimpleNamespace(player=self.player, reset=Mock()),
            spatial=None, ticket=0, session=self.session, latency=SimpleNamespace(start=Mock()))
        self.provider = SimpleNamespace(generate_visual=lambda instructions, query, frames, context:
            dict(text='A cup is visible.', evidence_frame_ids=[f['id'] for f in frames], insufficient_evidence=False))
        self.bridge = OrchestrationBridge(self.worker, SimpleNamespace(route=lambda *a: Route('find', 'red cup')), self.provider)
        self.addCleanup(self.bridge.close)

    def start_find(self):
        now = time.monotonic()
        packet = SimpleNamespace(image=Image.new('RGB', (32, 24)), stamp=SimpleNamespace(
            frame_id='SIMULATED:1', stream_id='SIMULATED', connection_epoch=1, rotation_clockwise=0,
            received_monotonic_s=now, image_width=32, image_height=24))
        self.bridge.observe(packet)
        ctx = self.bridge.coordinator.submit('Find cup')
        end = time.monotonic() + 2
        while self.bridge.coordinator.snapshot()['status'] != 'pending' and time.monotonic() < end:
            threading.Event().wait(.005)
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'pending')
        self.bridge.drain()
        self.job = SimpleNamespace(generation=self.session.generation, epoch=self.session.epoch,
            received=time.monotonic(), frame_id='reference', image=Image.new('RGB', (32, 24)))
        self.session.inflight = self.job
        return ctx

    def result(self, pending=False):
        return dict(mode='HYBRID', providerPending=pending, provider={'ok': True},
            screenModel={'frameId': 'reference', 'coordinateSystem': 'normalizedImageTopLeft',
                         'image': {'widthPx': 32, 'heightPx': 24}},
            selection={'status': 'SELECTED', 'target': {'label': 'cup', 'frameId': 'reference',
                       'imageRegion': {'x': .1, 'y': .1, 'width': .3, 'height': .4}}})

    def test_provisional_local_candidate_held_until_gemini_verifies(self):
        ctx = self.start_find()
        self.assertIsNone(self.bridge.filter_result(self.job, self.result(pending=True)))
        self.assertIsNone(self.session.tracker)
        result = self.bridge.filter_result(self.job, {'mode': 'HYBRID_VERIFY', 'agrees': True})
        self.assertFalse(result['providerPending'])
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'handoff')
        # Handoff is only a reference. Existing local tracker must independently lock.
        self.assertIsNone(self.session.tracker)
        self.session.state, self.session.label = 'LOCKED', 'cup'
        observed = time.monotonic()
        self.worker.latest_frame = SimpleNamespace(stamp=SimpleNamespace(
            frame_id='SIMULATED:lock', received_monotonic_s=observed))
        self.bridge.tracking(observed)
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'tracking')
        self.session.state = 'TARGET LOST'
        self.bridge.tracking(time.monotonic())
        self.assertFalse(ctx.is_current())
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'target_lost')
        self.player.enable_guidance.assert_called_with(False)

    def test_ambiguous_result_requests_clarification_without_tracker(self):
        self.start_find()
        reply = self.result()
        reply['selection'] = {'status': 'AMBIGUOUS', 'candidateLabels': ['red cup', 'blue cup']}
        self.assertIsNone(self.bridge.filter_result(self.job, reply))
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'clarify')
        self.assertIn('red cup', self.bridge.coordinator.snapshot()['clarification'])
        self.assertIsNone(self.session.tracker)

    def test_stop_prevents_late_target_handoff(self):
        self.start_find()
        self.bridge.coordinator.stop()
        self.assertIsNone(self.bridge.filter_result(self.job, self.result()))

    def test_cancelled_provisional_retains_slot_until_provider_finishes(self):
        self.start_find()
        self.bridge.coordinator.stop()
        self.assertIsNone(self.bridge.filter_result(self.job, self.result(pending=True)))
        self.assertIs(self.session.inflight, self.job)
        self.assertIsNone(self.bridge.filter_result(self.job, {'mode': 'HYBRID_VERIFY', 'agrees': True}))
        self.assertIsNone(self.session.inflight)
        self.player.interrupt_all.assert_called()
        self.bridge.drain()
        self.assertEqual(self.session.query, '')

    def test_old_inflight_reply_does_not_fail_replacement_task(self):
        old_context = self.start_find()
        old_job = self.job
        new_context = self.start_find()
        self.assertFalse(old_context.is_current())
        self.assertTrue(new_context.is_current())
        # The real tracker retains the one occupied provider slot until old work exits.
        self.session.inflight = old_job
        self.assertIsNone(self.bridge.filter_result(old_job, self.result()))
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'pending')
        self.assertTrue(new_context.is_current())
        self.assertIsNone(self.session.inflight)

    def test_stale_dequeued_request_stops_local_state(self):
        ctx = self.start_find()
        self.bridge.coordinator.stop()
        # A cancelled command already dequeued by a local worker is also harmless.
        self.bridge._replace(('obsolete cup', ctx))
        self.bridge.drain()
        self.assertEqual(self.session.query, '')
        self.assertIsNone(self.bridge.context)
        self.player.enable_guidance.assert_called_with(False, context=None)

    def test_tracking_expires_at_300ms_with_actual_observation_time(self):
        ctx = self.start_find()
        received = time.monotonic()
        self.worker.latest_frame = SimpleNamespace(stamp=SimpleNamespace(
            frame_id='SIMULATED:lock', received_monotonic_s=received))
        self.session.state, self.session.label = 'LOCKED', 'cup'
        self.bridge.tracking(received + .1)
        self.assertEqual(self.bridge.coordinator.snapshot()['observed_at'], received)
        self.bridge.tracking(received + .301)
        self.assertFalse(ctx.is_current())
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'target_lost')

    def test_initial_stale_lock_never_reports_tracking(self):
        ctx = self.start_find()
        received = time.monotonic()
        self.worker.latest_frame = SimpleNamespace(stamp=SimpleNamespace(
            frame_id='SIMULATED:lock', received_monotonic_s=received))
        self.session.state, self.session.label = 'LOCKED', 'cup'
        self.bridge.tracking(received + .301)
        self.assertNotEqual(self.bridge.coordinator.snapshot()['status'], 'tracking')

    def test_wrong_epoch_invalid_geometry_and_stale_results(self):
        for failure in ('epoch', 'geometry', 'stale'):
            with self.subTest(failure=failure):
                self.start_find()
                reply = self.result()
                if failure == 'epoch':
                    self.job.epoch = ('SIMULATED', 2)
                elif failure == 'geometry':
                    reply['selection']['target']['imageRegion']['x'] = .99
                else:
                    self.job.received -= 6
                self.assertIsNone(self.bridge.filter_result(self.job, reply))
                self.assertIsNone(self.session.tracker)

    def test_deadline_during_report_does_not_release_target(self):
        self.start_find()
        # Keep source fresh but expire the task just before its result is accepted.
        self.bridge.coordinator.deadline = time.monotonic() - .01
        self.assertIsNone(self.bridge.filter_result(self.job, self.result()))
        self.assertEqual(self.bridge.coordinator.snapshot()['error'], 'PROVIDER_TIMEOUT')

    def test_provider_disagreement_requires_clarification(self):
        self.start_find()
        self.bridge.filter_result(self.job, self.result(pending=True))
        self.assertIsNone(self.bridge.filter_result(self.job, {'mode': 'HYBRID_VERIFY', 'agrees': False}))
        self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'clarify')
        self.assertIsNone(self.session.tracker)

    def test_shared_frames_feed_scene_and_bounded_recent_recall(self):
        now = [time.monotonic()]
        self.bridge.frames.clock = lambda: now[0]
        for index in range(2):
            packet = SimpleNamespace(image=Image.new('RGB', (32, 24)), stamp=SimpleNamespace(
                frame_id=f'SIMULATED:{index}', stream_id='SIMULATED', connection_epoch=1,
                rotation_clockwise=0, received_monotonic_s=now[0], image_width=32, image_height=24))
            self.bridge.observe(packet)
            if index == 0:
                now[0] += 1.1
        for request, expected in [('Describe surroundings', 'In the image'),
                                  ('What did I miss?', 'From sampled images')]:
            with self.subTest(request=request):
                self.bridge.coordinator.submit(request)
                end = time.monotonic() + 2
                while self.bridge.coordinator.snapshot()['status'] != 'completed' and time.monotonic() < end:
                    threading.Event().wait(.005)
                self.assertEqual(self.bridge.coordinator.snapshot()['status'], 'completed')
                self.assertIn(expected, self.bridge.coordinator.snapshot()['response'])
                self.assertIsNone(self.session.tracker)


if __name__ == '__main__':
    unittest.main()
