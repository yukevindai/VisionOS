"""ALL OBJECTS mode with SIMULATED detections and Gemini replies; no model or API calls."""
import argparse
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from r3_vision import all_objects, hybrid
from r3_vision.all_objects import VERIFIED, AllObjects, Inventory, SelectionLatency
from r3_vision.lock_session import LockSession
from r3_vision.tracking_session import IdentifyJob, RequestBudget

SIZE = (1000, 800)


def det(names, box, score=.8):
    return {'name': names[0], 'names': list(names), 'score': score, 'box': box}


def gemini_reply(items, ok=True):
    """items: (label, normalized x0, y0, x1, y1)."""
    controls = [{'id': f'el-{i:03d}', 'label': label, 'legibility': 'clear', 'type': 'other',
                 'region': {'x': b[0], 'y': b[1], 'width': b[2] - b[0], 'height': b[3] - b[1]}}
                for i, (label, *b) in enumerate(items, 1)]
    return {'provider': {'ok': ok}, 'screenModel': {'controls': controls}}


CANS = [det(['can'], (100, 400, 200, 600)), det(['can', 'cup'], (300, 400, 400, 600)),
        det(['laptop'], (600, 100, 950, 500))]


def verified_inventory():
    inv = Inventory()
    inv.update(CANS, 1., SIZE)
    inv.update(CANS, 1.5, SIZE)
    snapshot = {t.id: (t.box, t.version) for t in inv.visible()}
    inv.apply_verification(snapshot, gemini_reply([('Red Bull can (drink, energy drink)', .1, .5, .2, .75),
                                                   ('Pepsi can (drink, soda)', .3, .5, .4, .75),
                                                   ('HP laptop (computer)', .6, .125, .95, .625)]))
    return inv


class InventoryTests(unittest.TestCase):
    def test_ids_stay_stable_and_new_objects_get_new_ids(self):
        inv = Inventory()
        first = inv.update(CANS, 1., SIZE)
        moved = [det(c['names'], (c['box'][0] + 8, c['box'][1] + 5, c['box'][2] + 8, c['box'][3] + 5)) for c in CANS]
        second = inv.update(moved + [det(['mug'], (450, 600, 520, 700))], 1.5, SIZE)
        self.assertEqual([second[i] for i in range(3)], [first[i] for i in range(3)])
        self.assertEqual(second[3], 4)
        self.assertEqual(inv.tracks[1].hits, 2)

    def test_camera_motion_keeps_ids_through_a_large_pan(self):
        inv = Inventory()
        inv.update(CANS, 1., SIZE)
        panned = [det(c['names'], (c['box'][0] + 150, c['box'][1], c['box'][2] + 150, c['box'][3])) for c in CANS]
        motion = np.float32([[1, 0, 150 * 320 / 1000], [0, 1, 0]])  # in the 320 x 256 grey frame
        ids = inv.update(panned, 1.5, SIZE, motion, (256, 320))
        self.assertEqual(ids, {0: 1, 1: 2, 2: 3})
        without = Inventory()
        without.update(CANS, 1., SIZE)
        self.assertNotEqual(without.update(panned, 1.5, SIZE), {0: 1, 1: 2, 2: 3})

    def test_missing_objects_expire_and_old_frames_are_ignored(self):
        inv = Inventory(max_missing_s=2)
        inv.update(CANS, 1., SIZE)
        self.assertEqual(inv.update(CANS[:1], .5, SIZE), {})
        inv.update(CANS[:1], 2., SIZE)
        self.assertEqual(len(inv.visible()), 1)
        self.assertEqual(len(inv.present()), 3)
        inv.update(CANS[:1], 4., SIZE)
        self.assertEqual(sorted(inv.tracks), [1])

    def test_verification_matches_gemini_boxes_on_the_same_frame(self):
        inv = verified_inventory()
        self.assertEqual([inv.tracks[i].status for i in (1, 2, 3)], [VERIFIED] * 3)
        self.assertEqual(inv.tracks[1].label, 'Red Bull can (drink, energy drink)')
        self.assertEqual(inv.tracks[1].shown_status(), 'VERIFIED')

    def test_unmatched_or_changed_objects_are_not_verified(self):
        inv = Inventory()
        inv.update(CANS, 1., SIZE)
        snapshot = {t.id: (t.box, t.version) for t in inv.visible()}
        inv.tracks[3].version += 1  # changed while Gemini was looking
        counts = inv.apply_verification(snapshot, gemini_reply([('Red Bull can', .1, .5, .2, .75),
                                                                ('laptop', .6, .125, .95, .625),
                                                                ('banana', .0, .0, .05, .05)]))
        self.assertEqual(counts, dict(verified=1, unconfirmed=1, error=False))
        self.assertEqual(inv.tracks[2].shown_status(), 'UNVERIFIED (Gemini did not confirm)')
        self.assertNotEqual(inv.tracks[3].status, VERIFIED)
        self.assertEqual(inv.gemini_only, ['banana'])  # Gemini objects with no detected box
        self.assertEqual([t.id for t in inv.needs_check(snapshot)], [])  # 1 pass only: not stable yet
        failed = Inventory()
        failed.update(CANS, 1., SIZE)
        self.assertTrue(failed.apply_verification(snapshot, gemini_reply([], ok=False))['error'])

    def test_jump_or_crowding_withdraws_verification(self):
        inv = verified_inventory()
        jumped = [det(['can'], (175, 400, 275, 600)), *CANS[1:]]  # IoU .14: centre match only
        inv.update(jumped, 2., SIZE)
        self.assertEqual(inv.tracks[1].status, 'UNVERIFIED')
        self.assertIn('re-check', inv.tracks[1].shown_status())
        self.assertEqual(inv.tracks[2].status, VERIFIED)

    def test_reappearing_after_a_long_absence_needs_a_new_check(self):
        inv = verified_inventory()
        inv.update(CANS[1:], 2.5, SIZE)
        inv.update(CANS, 3., SIZE)  # can #1 unseen for 1.5 s: brief cover keeps the verification
        self.assertEqual(inv.tracks[1].status, VERIFIED)
        inv.update(CANS, 6., SIZE)  # a 3 s pause in discovery itself is not an absence
        self.assertEqual(inv.tracks[1].status, VERIFIED)
        inv.update(CANS[1:], 6.5, SIZE)
        inv.update(CANS[1:], 7., SIZE)
        inv.update(CANS, 8.6, SIZE)  # missed by two passes and unseen for 2.6 s
        self.assertEqual(inv.tracks[1].shown_status(), 'UNVERIFIED (reappeared; re-check)')


class LookupTests(unittest.TestCase):
    def test_verified_brand_match_is_chosen(self):
        kind, track, _ = verified_inventory().lookup('Find the Red Bull can')
        self.assertEqual((kind, track.id), ('VERIFIED', 1))
        kind, track, _ = verified_inventory().lookup('Find the energy drink')
        self.assertEqual((kind, track.id), ('VERIFIED', 1))

    def test_several_verified_matches_fall_back(self):
        self.assertIsNone(verified_inventory().lookup('Find the can')[0])

    def test_similar_unverified_object_blocks_the_inventory_choice(self):
        inv = verified_inventory()
        inv.update(CANS + [det(['can'], (450, 400, 520, 600))], 2., SIZE)
        kind, track, reason = inv.lookup('Find the Red Bull can')
        self.assertIsNone(kind)
        self.assertIn('#4', reason)
        inv.update(CANS + [det(['can'], (450, 400, 520, 600))], 2.5, SIZE)
        inv.apply_verification({4: (inv.tracks[4].box, inv.tracks[4].version)}, gemini_reply([]))
        self.assertEqual(inv.lookup('Find the Red Bull can')[0], 'VERIFIED')  # Gemini saw it and did not list it

    def test_generic_unique_plain_class_only_with_trusted_labels(self):
        inv = Inventory()
        inv.update([det(['laptop'], (600, 100, 950, 500)), det(['cup'], (0, 0, 50, 50))], 1., SIZE)
        self.assertEqual(inv.lookup('Find the laptop')[0], 'GENERIC')
        self.assertIsNone(inv.lookup('Find the black laptop')[0])
        prompt_free = Inventory(labels_trusted=False)
        prompt_free.update([det(['laptop'], (600, 100, 950, 500))], 1., SIZE)
        self.assertIsNone(prompt_free.lookup('Find the laptop')[0])
        self.assertIsNone(Inventory().lookup('Find the laptop')[0])


def packet(image, received):
    return SimpleNamespace(image=image, stamp=SimpleNamespace(received_monotonic_s=received))


class FakeDetector:
    model = object()
    status = 'READY'

    def __init__(self, candidates):
        import threading
        self.candidates, self.lock, self.calls = candidates, threading.Lock(), 0

    def detect(self, image, phrases):
        self.calls += 1
        sx, sy = image.width / SIZE[0], image.height / SIZE[1]
        return [dict(c, box=(c['box'][0] * sx, c['box'][1] * sy, c['box'][2] * sx, c['box'][3] * sy))
                for c in self.candidates], 120.


def runtime(candidates=CANS, budget=None, **extra):
    args = argparse.Namespace(discovery_interval=.5, discovery_conf=.25, verify_interval=15, verify_reserve=5,
                              rotation=0, host=None, **extra)
    return AllObjects(args, FakeDetector(candidates), object(), budget or RequestBudget(0, 8, 20),
                      Path(tempfile.mkdtemp()), 'SIMULATED', start=False, log=lambda *a, **k: None)


class RuntimeTests(unittest.TestCase):
    def test_discovery_is_adaptive_and_skips_stale_or_busy(self):
        rt = runtime()
        image = Image.new('RGB', (640, 512))
        self.assertEqual(rt.discover_once(), .5)  # no frame yet
        rt.offer(packet(image, 10.), image, {})
        self.assertGreaterEqual(rt.discover_once(now=10.05), .5)
        self.assertEqual((rt.passes, len(rt.inventory.visible())), (1, 3))
        rt.discover_once(now=10.1)
        self.assertEqual(rt.skips['no fresh frame'], 1)  # same frame again
        rt.offer(packet(image, 11.), image, {})
        rt.discover_once(now=11.5)
        self.assertEqual(rt.skips['no fresh frame'], 2)  # 500 ms old
        rt.health = lambda: 300.
        rt.offer(packet(image, 12.), image, {})
        self.assertGreater(rt.discover_once(now=12.01), .5)
        self.assertEqual((rt.skips['tracking loop busy'], rt.detector.calls), (1, 1))

    def test_verification_is_rate_limited_and_keeps_requests_for_find(self):
        budget = RequestBudget(0, 8, 7)
        rt = runtime(budget=budget)
        image = Image.new('RGB', (640, 512))
        for i in range(2):
            rt.offer(packet(image, 10. + i), image, {})
            rt.discover_once(now=10.01 + i)
        started = []
        session = SimpleNamespace(inflight=None)
        self.assertTrue(rt.maybe_verify(11.1, session, start=lambda: started.append(1)))
        self.assertEqual((budget.total, rt.inventory.tracks[1].shown_status()), (1, 'UNVERIFIED (checking)'))
        self.assertFalse(rt.maybe_verify(11.2, session, start=lambda: started.append(1)))  # one in flight
        rt.verifying = False
        self.assertFalse(rt.maybe_verify(20., session, start=lambda: started.append(1)))  # verify interval
        busy = SimpleNamespace(inflight=object())
        self.assertFalse(rt.maybe_verify(40., busy, start=lambda: started.append(1)))
        budget.total = 2  # 7 - 5 reserved
        rt.snapshot['received'] = 40.
        for track in rt.inventory.tracks.values():
            track.checking = False
        self.assertFalse(rt.maybe_verify(40.1, session, start=lambda: started.append(1)))
        self.assertIn('kept for Find', rt.verify_note)
        self.assertEqual(started, [1])

    def test_nothing_new_means_no_gemini_call(self):
        rt = runtime()
        image = Image.new('RGB', (640, 512))
        for i in range(2):
            rt.offer(packet(image, 10. + i), image, {})
            rt.discover_once(now=10.01 + i)
        rt.inventory.apply_verification(rt.snapshot['boxes'], gemini_reply([]))
        self.assertFalse(rt.maybe_verify(11.5, SimpleNamespace(inflight=None), start=lambda: None))
        self.assertIn('nothing new', rt.verify_note)

    def test_choose_uses_the_box_detected_on_the_request_frame(self):
        rt = runtime()
        rt.inventory = verified_inventory()
        moved = [det(c['names'], (c['box'][0] + 10, c['box'][1], c['box'][2] + 10, c['box'][3])) for c in CANS]
        rt.detector.candidates = moved
        job = IdentifyJob(1, 'track-SIMULATED', Image.new('RGB', (2000, 1600)), 'Find the Red Bull can', 3.,
                          ('e', SIZE, 0))
        choice = rt.choose(job)
        self.assertEqual((choice['kind'], choice['candidate']['id']), ('VERIFIED', 1))
        self.assertEqual([round(v) for v in choice['candidate']['box']], [220, 800, 420, 1200])
        rt.detector.candidates = moved[1:]
        self.assertIsNone(rt.choose(job)['kind'])  # not seen on this frame: never an old box

    def test_overlay_shows_id_label_confidence_and_status(self):
        rt = runtime()
        rt.inventory = verified_inventory()
        rt.inventory.update(CANS + [det(['mug'], (450, 600, 520, 700), .41)], 2., SIZE)
        fresh, stale = Image.new('RGB', SIZE), Image.new('RGB', SIZE)
        rt.draw(fresh, 2.1)
        rt.draw(stale, 3.)
        cyan = lambda im: int(np.all(np.array(im) == (0, 200, 255), axis=2).sum())
        self.assertGreater(cyan(fresh), cyan(stale) > 0)
        self.assertIn('4 objects, 3 verified', rt.summary())
        track = rt.inventory.tracks[4]
        self.assertEqual(f'#{track.id} {track.shown_label()} {track.score:.2f} {track.shown_status()}',
                         '#4 mug 0.41 UNVERIFIED')


class WorkerTests(unittest.TestCase):
    def worker(self, choice):
        from r3_vision.live_tracking import TrackingWorker
        w = TrackingWorker.__new__(TrackingWorker)
        w.responses = queue.Queue()
        w.hybrid_note = ''
        w.args = argparse.Namespace(output=Path(tempfile.mkdtemp()), timeout=5, target_kind='object')
        w.all_objects = SimpleNamespace(choose=lambda job: choice)
        w.latency = SelectionLatency(w.args.output / 'sel.jsonl', 'allobjects', log=lambda *a: None)
        w.detector = SimpleNamespace(model=object(), status='READY', detect=lambda image, words: ([], 1.))
        w.calls = []
        w._gemini_result = lambda *a: w.calls.append('gemini') or {}
        w._identify_hybrid = lambda *a, **k: w.calls.append(('hybrid', k.get('local')))
        return w

    def job(self, query='Find the Red Bull can'):
        return IdentifyJob(1, 'track-SIMULATED', Image.new('RGB', SIZE), query, 1., ('e', SIZE, 0))

    def test_verified_object_locks_without_a_gemini_call(self):
        inv = verified_inventory()
        candidate = dict(CANS[0], id=1)
        w = self.worker(dict(kind='VERIFIED', track=inv.tracks[1], candidate=candidate, candidates=[candidate],
                             ms=130., reason='#1 verified'))
        self.assertTrue(w._identify_allobjects(self.job(), None, {}, w.args.output / 'f'))
        _, result, _ = w.responses.get_nowait()
        self.assertEqual(w.calls, [])
        self.assertEqual((result['mode'], result['detectionSource']), ('HYBRID', 'INVENTORY #1 VERIFIED'))
        self.assertEqual(result['selection']['target']['label'], 'Red Bull can (drink, energy drink)')
        self.assertEqual(result['selection']['target']['imageRegion'], hybrid.region(CANS[0]['box'], SIZE))
        session = LockSession(RequestBudget(0, 60, 100), reacquire_after=0, tracker_factory=lambda image, area: object())
        session.find('Find the Red Bull can')
        session.epoch = ('e', SIZE, 0)
        job = session.job(Image.new('RGB', SIZE), 1., 1.)
        result = dict(result, screenModel=dict(result['screenModel'], frameId=job.frame_id),
                      selection=dict(result['selection'], target=dict(result['selection']['target'],
                                                                      frameId=job.frame_id)))
        session.complete(job, result, 1.2)
        self.assertEqual((session.state, session.label), ('REACQUIRING', 'Red Bull can (drink, energy drink)'))
        self.assertIn('no Gemini call', session.last_identification)

    def test_generic_choice_uses_hybrid_with_current_candidates_and_fallback_otherwise(self):
        inv = Inventory()
        inv.update([det(['laptop'], (600, 100, 950, 500))], 1., SIZE)
        cands = [dict(det(['laptop'], (600, 100, 950, 500)), id=1)]
        w = self.worker(dict(kind='GENERIC', track=inv.tracks[1], candidate=cands[0], candidates=cands, ms=120.,
                             reason='#1 one laptop detected'))
        self.assertTrue(w._identify_allobjects(self.job('Find the laptop'), None, {}, w.args.output / 'f'))
        self.assertEqual(w.calls, [('hybrid', (cands, 120., 'INVENTORY #1 LOCAL'))])
        fallback = self.worker(dict(kind=None, track=None, candidate=None, candidates=[], ms=0.,
                                    reason='no verified match'))
        self.assertFalse(fallback._identify_allobjects(self.job('Find the black tumbler'), None, {}, None))
        self.assertIn('HYBRID request flow', fallback.hybrid_note)

    def test_hybrid_flow_accepts_inventory_candidates_and_keeps_its_source(self):
        from r3_vision.live_tracking import TrackingWorker
        w = TrackingWorker.__new__(TrackingWorker)
        w.responses = queue.Queue()
        w.hybrid_note = ''
        w.detector = SimpleNamespace(detect=lambda *a: self.fail('must not detect again'))
        w.args = argparse.Namespace(output=Path(tempfile.mkdtemp()), timeout=5, target_kind='object')
        w._gemini_result = lambda *a: {'provider': {'ok': True}, 'selection': {'status': 'SELECTED', 'target': {
            'label': 'laptop', 'imageRegion': hybrid.region((600, 100, 950, 500), SIZE)}}, 'timingMs': {'provider': 1.}}
        cands = [dict(det(['laptop'], (600, 100, 950, 500)), id=7)]
        w._identify_hybrid(self.job('Find the laptop'), None, {}, w.args.output / 'f',
                           local=(cands, 90., 'INVENTORY #7 LOCAL'))
        first, second = w.responses.get_nowait()[1], w.responses.get_nowait()[1]
        self.assertEqual((first['detectionSource'], first['selection']['target']['detectorId']),
                         ('INVENTORY #7 LOCAL', 7))
        self.assertIs(second['agrees'], True)


class LatencyTests(unittest.TestCase):
    def test_verified_lock_time_per_source(self):
        records = []
        latency = SelectionLatency(Path(tempfile.mkdtemp()) / 's.jsonl', 'allobjects',
                                   log=lambda *a: records.append(a))
        session = SimpleNamespace(time_to_lock=None, source='')
        latency.start('Find the Red Bull can', 100.)
        self.assertIsNone(latency.update(session))
        session.time_to_lock, session.source = .41, 'INVENTORY #1 VERIFIED'
        self.assertEqual(latency.update(session)['verifiedS'], .41)
        latency.start('Find the laptop', 200.)
        session.time_to_lock, session.source = .3, 'YOLO'
        self.assertIsNone(latency.update(session))  # local lock: not verified until Gemini agrees
        latency.confirmed(206.5)
        self.assertEqual(latency.update(session)['verifiedS'], 6.5)
        latency.start('Find the Pepsi can', 300.)
        session.time_to_lock, session.source = 7.2, 'GEMINI+YOLO box'
        self.assertEqual(latency.update(session)['verifiedS'], 7.2)
        self.assertEqual(len(records), 3)
        self.assertEqual(len(latency.path.read_text().splitlines()), 3)


class ArgumentTests(unittest.TestCase):
    def test_allobjects_arguments(self):
        from r3_vision.live_tracking import arguments
        args = arguments(['--video', '0', '--detection', 'allobjects', '--discovery-source', 'yoloe'])
        self.assertEqual((args.detection, args.discovery_source, args.discovery_interval, args.verify_reserve),
                         ('allobjects', 'yoloe', 1., 5))
        with self.assertRaises(SystemExit):
            arguments(['--video', '0', '--discovery-interval', '0'])

    def test_vocabulary_is_general_and_has_no_people(self):
        self.assertGreater(len(all_objects.VOCABULARY), 40)
        self.assertNotIn('person', all_objects.VOCABULARY)
        self.assertEqual(len(set(all_objects.VOCABULARY)), len(all_objects.VOCABULARY))


if __name__ == '__main__':
    unittest.main()
