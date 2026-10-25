"""SIMULATED detector candidates and Gemini answers; no model or API calls."""
import argparse
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from r3_vision import hybrid
from r3_vision.local_detector import merge
from r3_vision.lock_session import LockSession
from r3_vision.tracking_session import IdentifyJob, RequestBudget

SIZE = (1000, 800)


def cand(i, names, box, score=.8):
    return {'id': i, 'name': names[0], 'names': list(names), 'score': score, 'box': box}


def job(query='Find the laptop'):
    return IdentifyJob(1, 'track-SIMULATED', Image.new('RGB', SIZE), query, 1., ('e', SIZE, 0))


def gemini_answer(status='SELECTED', area=None, ok=True, label='HP laptop (electronics)'):
    selection = {'status': status, 'reason': None if status == 'SELECTED' else 'NO_MATCH'}
    if area is not None:
        selection['target'] = {'label': label, 'imageRegion': area, 'frameId': 'track-SIMULATED'}
    return {'mode': 'REAL_API', 'provider': {'ok': ok}, 'selection': selection, 'timingMs': {'provider': 6400.},
            'screenModel': {'frameId': 'track-SIMULATED', 'controls': []}}


class DecisionTests(unittest.TestCase):
    def test_relation_parse_and_vocabulary(self):
        self.assertEqual(hybrid.parse('Find the cup beside my laptop'), ('cup', 'near', 'laptop'))
        self.assertEqual(hybrid.phrases('Find the black tumbler'), ['black tumbler', 'tumbler'])
        self.assertEqual(hybrid.parse('Find the bagel on the plate')[1:], ('on', 'plate'))

    def test_only_a_unique_plainly_named_object_locks_locally(self):
        laptop = cand(1, ['laptop'], (0, 0, 500, 400))
        self.assertIs(hybrid.local_choice('Find the laptop', [laptop])[0], laptop)
        two = [cand(1, ['bagel'], (0, 0, 50, 50)), cand(2, ['bagel'], (100, 0, 150, 50))]
        self.assertIsNone(hybrid.local_choice('Find the bagel', two)[0])
        self.assertIsNone(hybrid.local_choice('Find the cup', [cand(1, ['cup', 'can'], (0, 0, 9, 9))])[0])
        self.assertIsNone(hybrid.local_choice('Find the Red Bull can', [cand(1, ['can'], (0, 0, 9, 9))])[0])
        self.assertIsNone(hybrid.local_choice('Find the cup beside the laptop',
                                              [cand(1, ['cup'], (0, 0, 9, 9)), laptop])[0])
        self.assertIsNone(hybrid.local_choice('Find the banana', [laptop])[0])

    def test_merge_keeps_one_box_per_object(self):
        merged = merge([cand(0, ['cup'], (0, 0, 100, 100), .9), cand(0, ['can'], (2, 1, 101, 99), .5),
                        cand(0, ['laptop'], (300, 0, 600, 300), .7)])
        self.assertEqual([m['names'] for m in merged], [['cup', 'can'], ['laptop']])
        self.assertEqual([m['id'] for m in merged], [1, 2])

    def test_verify_agrees_disagrees_or_unavailable(self):
        local = cand(1, ['laptop'], (500, 400, 1000, 800))
        same = {'x': .5, 'y': .5, 'width': .5, 'height': .5}
        other = {'x': 0., 'y': 0., 'width': .2, 'height': .2}
        self.assertIs(hybrid.verify(gemini_answer(area=same), local, SIZE)['agrees'], True)
        self.assertIs(hybrid.verify(gemini_answer(area=other), local, SIZE)['agrees'], False)
        self.assertIs(hybrid.verify(gemini_answer('NOT_FOUND'), local, SIZE)['agrees'], False)
        self.assertIsNone(hybrid.verify(gemini_answer(ok=False), local, SIZE)['agrees'])

    def test_snap_uses_overlapping_local_box_only(self):
        area = {'x': .1, 'y': .1, 'width': .2, 'height': .2}
        snapped = hybrid.snap(gemini_answer(area=area), [cand(1, ['cup'], (105, 85, 305, 245))], SIZE, 1400.)
        self.assertEqual(snapped['detectionSource'], 'GEMINI+YOLO box')
        self.assertEqual(snapped['selection']['target']['imageRegion'], hybrid.region((105, 85, 305, 245), SIZE))
        self.assertEqual(snapped['selection']['target']['geminiRegion'], area)
        apart = hybrid.snap(gemini_answer(area=area), [cand(1, ['cup'], (700, 500, 900, 700))], SIZE, 1400.)
        self.assertEqual((apart['detectionSource'], apart['selection']['target']['imageRegion']), ('GEMINI', area))
        self.assertEqual(apart['timingMs']['local'], 1400.)


class SpeedChangeTests(unittest.TestCase):
    def test_of_phrases_keep_their_object_word(self):
        self.assertEqual(hybrid.head_noun('cup of coffee'), ('cup', []))
        self.assertEqual(hybrid.head_noun('black tumbler'), ('tumbler', ['black']))
        laptop = cand(1, ['cup'], (0, 0, 500, 400))
        self.assertIs(hybrid.local_choice('Find cup of coffee', [laptop])[0], laptop)

    def test_media_resolution_is_sent_only_when_set(self):
        import json
        from r3_vision import gemini
        bodies = []

        def transport(url, headers, body, timeout):
            bodies.append(json.loads(body))
            return 200, json.dumps({'candidates': [{'content': {'parts': [{'text': '{}'}]}}]}).encode()
        client = gemini.GeminiClient(api_key='k', model='gemini-3.8-flash', transport=transport, live=False)
        client.describe(b'jpeg')
        client.media_resolution = 'medium'
        client.describe(b'jpeg')
        self.assertNotIn('mediaResolution', bodies[0]['generationConfig'])
        self.assertEqual(bodies[1]['generationConfig']['mediaResolution'], 'MEDIA_RESOLUTION_MEDIUM')

    def test_fast_capture_sends_jpeg_and_still_writes_files(self):
        import io
        import time
        from r3_vision import live_camera
        seen = []
        original = live_camera.pipeline.run
        live_camera.pipeline.run = lambda data, *a, **k: seen.append(data[:3]) or {
            'selection': {'status': 'NOT_FOUND', 'reason': 'NO_MATCH'}, 'screenModel': {'controls': []},
            'mode': 'SIMULATED', 'timingMs': {'provider': 1.}, 'provider': {'ok': True}}
        try:
            folder = Path(tempfile.mkdtemp()) / 'cap'
            live_camera.process_capture(Image.new('RGB', (64, 48)), 0, 'Find x', {'frameID': 'f', 'snapshotUTCms': 1},
                                        None, folder, fast=True)
        finally:
            live_camera.pipeline.run = original
        self.assertEqual(seen, [b'\xff\xd8\xff'])
        self.assertTrue((folder / 'result.json').exists())
        deadline = time.monotonic() + 5
        while not (folder / 'annotated.png').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue((folder / 'captured.png').exists() and (folder / 'annotated.png').exists())


def _ultralytics_and_weights():
    import importlib.util
    from r3_vision.local_detector import WEIGHTS
    return importlib.util.find_spec('ultralytics') is not None and WEIGHTS.exists()


@unittest.skipUnless(_ultralytics_and_weights(), 'Ultralytics or YOLO-World weights not available in this Python')
class FastVocabularyTests(unittest.TestCase):
    def test_fast_class_switch_matches_ultralytics_set_classes(self):
        import numpy as np
        from ultralytics import YOLO
        from r3_vision.local_detector import ANCHORS, WEIGHTS, LocalDetector
        detector = LocalDetector(background=False)
        image = Image.new('RGB', (640, 480), (200, 200, 200))
        reference = YOLO(str(WEIGHTS))
        rng = np.random.default_rng(3)
        pixels = rng.integers(0, 255, (480, 640, 3), np.uint8)
        image = Image.fromarray(pixels)
        for words in (['black tumbler', 'tumbler'], ['laptop']):
            vocabulary = list(dict.fromkeys(words + list(ANCHORS)))
            reference.set_classes(vocabulary)
            fast, _ = detector.detect(image, words)
            slow = reference.predict(pixels[..., ::-1], imgsz=640, conf=.12, verbose=False, device='cpu')[0]
            self.assertEqual(detector.model.model.names, vocabulary)
            self.assertEqual(len(fast), len(merge([{'name': reference.names[int(c)], 'score': float(s),
                                                    'box': tuple(map(float, b))} for b, s, c in zip(
                slow.boxes.xyxy.numpy(), slow.boxes.conf.numpy(), slow.boxes.cls.numpy())])))


class HybridSessionTests(unittest.TestCase):
    def session(self, query='Find the laptop'):
        s = LockSession(RequestBudget(0, 60, 100), reacquire_after=0, tracker_factory=lambda image, area: object())
        s.find(query)
        s.epoch = ('e', SIZE, 0)
        return s

    def test_local_lock_then_gemini_disagreement_becomes_uncertain(self):
        s = self.session()
        j = s.job(Image.new('RGB', SIZE), 1., 1.)
        local = hybrid.result(j, 'SELECTED', 'YOLO', 'one laptop detected', {'local': 1400.}, [],
                              cand(1, ['laptop'], (0, 0, 500, 400)))
        s.complete(j, local, 2.)
        self.assertEqual((s.state, s.source), ('REACQUIRING', 'YOLO'))
        self.assertIsNotNone(s.tracker)
        self.assertIn('waiting for Gemini check', s.last_identification)
        s.complete(j, {'mode': 'HYBRID_VERIFY', 'agrees': False, 'detail': 'Gemini chose another object',
                       'timingMs': {'provider': 6000.}}, 8.)
        self.assertEqual(s.state, 'UNCERTAIN')
        self.assertIsNone(s.tracker)
        self.assertIsNone(s.job(Image.new('RGB', SIZE), 20., 20.))

    def test_gemini_confirmation_is_recorded(self):
        s = self.session()
        j = s.job(Image.new('RGB', SIZE), 1., 1.)
        s.complete(j, hybrid.result(j, 'SELECTED', 'YOLO', 'one laptop detected', {'local': 1.}, [],
                                    cand(1, ['laptop'], (0, 0, 500, 400))), 2.)
        s.complete(j, {'mode': 'HYBRID_VERIFY', 'agrees': True, 'timingMs': {'provider': 6000.}}, 8.)
        self.assertIn('Gemini confirmed (6.0s)', s.last_identification)
        self.assertIsNotNone(s.tracker)

    def test_early_hybrid_keeps_cloud_slot_across_new_find_and_old_verification(self):
        s = self.session()
        image = Image.new('RGB', SIZE)
        first = s.job(image, 1., 1.)
        local = hybrid.result(first, 'SELECTED', 'YOLO', 'one laptop', {'local': 1.}, [],
                              cand(1, ['laptop'], (0, 0, 500, 400)))
        s.complete(first, local, 2.)
        self.assertIs(s.inflight, first)
        s.find('Find the cup')
        self.assertIsNone(s.job(image, 3., 3.))
        s.complete(first, {'mode': 'HYBRID_VERIFY', 'agrees': None}, 4.)
        second = s.job(image, 4., 4.)
        self.assertIsNotNone(second)
        self.assertEqual(second.query, 'Find the cup')
        s.complete(first, {'mode': 'HYBRID_VERIFY', 'agrees': False}, 4.1)
        self.assertIs(s.inflight, second, 'duplicate old completion cannot release new work')
        self.assertEqual(s.state, 'IDENTIFYING')

    def test_stopped_hybrid_releases_only_on_terminal_verification(self):
        s = self.session()
        image = Image.new('RGB', SIZE)
        job = s.job(image, 1., 1.)
        s.stop()
        s.complete(job, hybrid.result(job, 'SELECTED', 'YOLO', 'one laptop', {}, [],
                                      cand(1, ['laptop'], (0, 0, 500, 400))), 2.)
        self.assertIs(s.inflight, job)
        self.assertIsNone(s.tracker)
        s.complete(job, {'mode': 'HYBRID_VERIFY', 'agrees': True}, 3.)
        self.assertIsNone(s.inflight)
        self.assertIsNone(s.tracker)

    def test_hybrid_result_for_another_frame_is_refused(self):
        s = self.session()
        j = s.job(Image.new('RGB', SIZE), 1., 1.)
        bad = hybrid.result(j, 'SELECTED', 'YOLO', 'x', {}, [], cand(1, ['laptop'], (0, 0, 9, 9)))
        bad['screenModel']['frameId'] = 'other-frame'
        s.complete(j, bad, 2.)
        self.assertEqual(s.state, 'ERROR')
        self.assertIsNone(s.tracker)


class WorkerFlowTests(unittest.TestCase):
    def worker(self, candidates, answer):
        from r3_vision.live_tracking import TrackingWorker
        w = TrackingWorker.__new__(TrackingWorker)
        w.responses = queue.Queue()
        w.hybrid_note = ''
        w.detector = SimpleNamespace(model=object(), status='READY', detect=lambda image, words: (candidates, 1400.))
        w._gemini_result = lambda job, raw, metadata, directory: answer
        w.args = argparse.Namespace(output=Path(tempfile.mkdtemp()), timeout=5, target_kind='object')
        return w

    def drain(self, w):
        items = []
        while not w.responses.empty():
            items.append(w.responses.get()[1])
        return items

    def test_local_tracker_does_not_release_a_still_running_provider(self):
        import threading
        laptop = cand(1, ['laptop'], (500, 400, 1000, 800))
        w = self.worker([laptop], None)
        release = threading.Event()
        def delayed(*_args):
            release.wait(2.)
            return gemini_answer(area={'x': .5, 'y': .5, 'width': .5, 'height': .5})
        w._gemini_result = delayed
        s = HybridSessionTests().session()
        image = Image.new('RGB', SIZE)
        first = s.job(image, 1., 1.)
        worker = threading.Thread(target=w._identify_hybrid, args=(first, None, {}, w.args.output / 'f'))
        worker.start()
        try:
            _, local, _ = w.responses.get(timeout=1.)
            s.complete(first, local, 2.)
            s.find('Find the cup')
            self.assertIsNone(s.job(image, 3., 3.))
            self.assertTrue(worker.is_alive())
            release.set()
            _, terminal, _ = w.responses.get(timeout=1.)
            s.complete(first, terminal, 4.)
            self.assertIsNotNone(s.job(image, 4., 4.))
        finally:
            release.set()
            worker.join(timeout=2.)

    def test_detector_failure_waits_for_provider_before_terminal_result(self):
        answer = gemini_answer()
        w = self.worker([], answer)
        def broken(*_args):
            raise RuntimeError('SIMULATED detector failure')
        w.detector.detect = broken
        w._identify_hybrid(job(), None, {}, w.args.output / 'f')
        self.assertEqual(self.drain(w), [answer])

    def test_local_lock_is_sent_before_the_gemini_check(self):
        laptop = cand(1, ['laptop'], (500, 400, 1000, 800))
        w = self.worker([laptop], gemini_answer(area={'x': .5, 'y': .5, 'width': .5, 'height': .5}))
        w._identify_hybrid(job(), None, {}, w.args.output / 'f')
        first, second = self.drain(w)
        self.assertEqual((first['mode'], first['detectionSource']), ('HYBRID', 'YOLO'))
        self.assertEqual((second['mode'], second['agrees']), ('HYBRID_VERIFY', True))

    def test_descriptive_request_waits_for_gemini_and_snaps_box(self):
        can = cand(1, ['cup'], (420, 330, 540, 700))
        area = {'x': .42, 'y': .41, 'width': .12, 'height': .46}
        w = self.worker([can], gemini_answer(area=area, label='Red Bull can (drink)'))
        w._identify_hybrid(job('Find the Red Bull can'), None, {}, w.args.output / 'f')
        (only,) = self.drain(w)
        self.assertEqual((only['mode'], only['detectionSource']), ('REAL_API', 'GEMINI+YOLO box'))
        self.assertEqual(only['timingMs']['local'], 1400.)


if __name__ == '__main__':
    unittest.main()
