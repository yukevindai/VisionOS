"""SIMULATED images/providers: no physical camera or Gemini acceptance claims."""
import copy
from concurrent.futures import CancelledError
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.visual_agents import SharedFrameService, VisualAgents, validate_object_result


class VisualAgentTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.frames = SharedFrameService(clock=lambda: self.now, max_frames=3, seconds=4.)
        self.ctx = SimpleNamespace(check=lambda: None)
        self.calls = []
        self.provider = SimpleNamespace(generate_visual=self.generate)
        self.agents = VisualAgents(self.frames, self.provider, lambda *args: self.calls.append(args))

    def packet(self, epoch=1, received=None):
        received = self.now if received is None else received
        stamp = SimpleNamespace(frame_id=f'SIMULATED:{epoch}:{received}', received_monotonic_s=received,
                                stream_id='SIMULATED', connection_epoch=epoch, rotation_clockwise=0,
                                image_width=32, image_height=24)
        return SimpleNamespace(stamp=stamp, image=Image.new('RGB', (32, 24)))

    def generate(self, instructions, query, frames, context):
        self.calls.append((instructions, query, frames))
        return dict(text='A cup is visible.', evidence_frame_ids=[f['id'] for f in frames],
                    insufficient_evidence=False)

    def test_fresh_frame_only_and_no_second_capture(self):
        self.assertEqual(self.agents.find('cup', self.ctx).status, 'unavailable')
        self.frames.publish(self.packet())
        self.assertEqual(self.agents.find('cup', self.ctx).status, 'pending')
        self.assertEqual(len(self.calls), 1)
        self.now += .301
        self.assertEqual(self.agents.find('cup', self.ctx).status, 'unavailable')
        self.assertEqual(len(self.calls), 1)

    def test_history_count_time_and_byte_bounds(self):
        for _ in range(8):
            self.frames.publish(self.packet())
            self.now += 1.
        history, _ = self.frames.recent()
        self.assertEqual(len(history), 3)
        self.assertLessEqual(sum(len(f.jpeg) for f in history), self.frames.max_bytes)
        self.now += 5
        self.assertEqual(self.frames.recent(), ((), ()))

    def test_reconnect_clears_timeline(self):
        packet = self.packet()
        self.frames.publish(packet)
        self.frames.event('locked', packet.stamp.frame_id, 'cup')
        self.now += 1
        self.frames.publish(self.packet(epoch=2))
        history, events = self.frames.recent()
        self.assertEqual(len(history), 1)
        self.assertEqual(events, ())

    def test_repeated_stale_future_frames_and_unmatched_events_rejected(self):
        packet = self.packet()
        self.assertTrue(self.frames.publish(packet))
        self.assertFalse(self.frames.publish(packet))
        self.assertFalse(self.frames.publish(self.packet(received=self.now - 1)))
        self.assertFalse(self.frames.publish(self.packet(received=self.now + 1)))
        self.assertFalse(self.frames.event('locked', 'wrong frame'))
        self.assertTrue(self.frames.event('locked', packet.stamp.frame_id))

    def test_scene_uses_shared_image_and_identified_observation(self):
        self.frames.publish(self.packet())
        result = self.agents.describe('What is on the table?', self.ctx)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.observed_at, 10.)
        self.assertIn('image from', result.text)
        self.assertTrue(self.calls[0][2][0]['jpeg'].startswith(b'\xff\xd8'))

    def test_recall_insufficiency_does_not_call_provider(self):
        self.frames.publish(self.packet())
        self.assertEqual(self.agents.recall('What did I miss?', self.ctx).status, 'insufficient_evidence')
        self.assertFalse(self.calls)

    def test_recall_timestamped_samples_require_citations(self):
        self.frames.publish(self.packet())
        self.now += 1
        self.frames.publish(self.packet())
        self.assertEqual(self.agents.recall('What did I miss?', self.ctx).status, 'completed')
        self.provider.generate_visual = lambda *args: dict(text='An invented story',
            evidence_frame_ids=['unseen-frame'], insufficient_evidence=False)
        self.assertEqual(self.agents.recall('What did I miss?', self.ctx).status, 'insufficient_evidence')

    def test_cancellation_during_provider_prevents_answer(self):
        self.frames.publish(self.packet())
        def generate(*args):
            self.ctx.check = lambda: (_ for _ in ()).throw(CancelledError())
            return dict(text='cup', evidence_frame_ids=[self.packet().stamp.frame_id], insufficient_evidence=False)
        self.provider.generate_visual = generate
        with self.assertRaises(CancelledError):
            self.agents.describe('describe', self.ctx)


class ReferenceHandoffTests(unittest.TestCase):
    def setUp(self):
        self.job = SimpleNamespace(generation=3, epoch=1, received=10., frame_id='frame',
                                   image=Image.new('RGB', (100, 80)))
        self.result = dict(mode='REAL_API', provider={'ok': True},
            screenModel=dict(frameId='frame', coordinateSystem='normalizedImageTopLeft',
                             image=dict(widthPx=100, heightPx=80)),
            selection=dict(status='SELECTED', target=dict(frameId='frame', label='cup',
                           imageRegion=dict(x=.1, y=.2, width=.3, height=.4))))

    def validate(self, result=None, **kwargs):
        values = dict(current_generation=3, current_epoch=1, now=11.)
        values.update(kwargs)
        return validate_object_result(self.job, result or self.result, **values)

    def test_handoff_is_reference_only(self):
        answer = self.validate()
        self.assertEqual(answer.status, 'handoff')
        self.assertIn('awaiting a current-frame match', answer.text)

    def test_old_generation_epoch_and_expired_reference_rejected(self):
        for kw in (dict(current_generation=4), dict(current_epoch=2), dict(now=16), dict(now=9)):
            self.assertEqual(self.validate(**kw).status, 'error')

    def test_invalid_coordinate_and_frame_provenance_rejected(self):
        for region in (dict(x=float('nan'), y=0, width=.1, height=.1),
                       dict(x=.9, y=0, width=.2, height=.1),
                       dict(x=True, y=0, width=.2, height=.1)):
            result = copy.deepcopy(self.result)
            result['selection']['target']['imageRegion'] = region
            self.assertEqual(self.validate(result).status, 'error')
        self.result['screenModel']['frameId'] = 'another'
        self.assertEqual(self.validate().status, 'error')

    def test_provisional_detection_never_handed_to_tracker(self):
        self.result.update(mode='HYBRID', providerPending=True)
        self.assertEqual(self.validate().status, 'pending')
        self.result['providerPending'] = False
        self.assertEqual(self.validate().status, 'handoff')

    def test_ambiguity_and_provider_failure(self):
        self.result['selection'] = dict(status='AMBIGUOUS', candidateLabels=['red cup', 'blue cup'])
        self.assertEqual(self.validate().status, 'clarify')
        self.result['provider']['ok'] = False
        self.assertEqual(self.validate().status, 'unavailable')


if __name__ == '__main__':
    unittest.main()
