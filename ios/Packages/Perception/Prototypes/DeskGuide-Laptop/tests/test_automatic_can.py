import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from deskguide.engine import Engine
from deskguide.models import ObjectDetector,ROOT
from deskguide.tracking import ObjectTracker
from deskguide.guidance import GuidanceOutput
import scene_fixtures as selected_fixtures


class AutomaticCanTests(unittest.TestCase):
    def test_verified_detector_parameters(self):
        calls=[]
        detector=ObjectDetector.__new__(ObjectDetector)
        detector.cpu_threads=2
        detector.threshold=.2
        detector.torch=SimpleNamespace(set_num_threads=lambda n:None)
        def predict(image,**kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(boxes=None)]
        detector.model=SimpleNamespace(predict=predict)
        detector.detect(np.zeros((32,32,3),np.uint8),'can')
        self.assertIsNone(calls[0]['classes'])
        self.assertEqual(calls[0]['imgsz'],640)
        self.assertEqual(calls[0]['conf'],.2)
        self.assertEqual(calls[0]['iou'],.7)
        self.assertFalse(calls[0]['agnostic_nms'])

    def test_selection_prefers_reach_then_continuity(self):
        cfg=json.loads((ROOT/'config.json').read_text())
        tracker=ObjectTracker(None,cfg)
        frame=selected_fixtures.scene()
        a=np.array([10.,20,90,140]); b=np.array([120.,20,200,140])
        frame.depth[:]=2.
        frame.depth[20:140,10:90]=.5
        chosen=tracker.choose_detection(frame,[(b,.9),(a,.3)],None)
        np.testing.assert_array_equal(chosen[0][0],a)
        frame.depth[:]=.5
        chosen=tracker.choose_detection(frame,[(b,.8),(a,.4)],a)
        np.testing.assert_array_equal(chosen[0][0],a)

    def test_default_engine_acquires_without_mouse_selection(self):
        scene=selected_fixtures.scene
        class Source:
            fps=sensor_fps=30.
            count=0
            @property
            def latest(self):
                self.count+=1
                f=scene(); f.id=self.count
                f.timestamp=f.received=time.monotonic()
                if self.count>=6: engine.stop.set()
                return f
        class Hand:
            last_ms=0.
            def detect(self,image): time.sleep(.003); return []
            def close(self): pass
        calls=[]
        class Detector:
            last_ms=1.
            def __init__(self,*args): pass
            def detect(self,image,target):
                calls.append(target)
                return [(np.array([110.,65,160,165]),.8)]
        cfg=json.loads((ROOT/'config.json').read_text()); cfg['rotation']=0
        self.assertEqual(cfg['detection_mode'],'automatic')
        (ROOT/'logs').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'logs',prefix='offline-auto-') as tmp:
            with patch('deskguide.engine.ROOT',Path(tmp)),patch('deskguide.engine.HandDetector',Hand),patch('deskguide.engine.ObjectDetector',Detector):
                engine=Engine(cfg,SimpleNamespace(status='SIMULATED',update=lambda *a,**k:None),source=Source())
                engine.run()
                self.assertEqual(engine.error,'')
                self.assertIsNotNone(engine.result['object_box'])
                self.assertIn(engine.result['metrics']['object_state'],('VISIBLE','TRACKED'))
                self.assertFalse(engine.result['metrics']['metric_valid'])
                self.assertLess(len(calls),7)
                self.assertFalse(engine.result['metrics']['detector_ran'])

    def run_paired(self,delay,estimated=False,lost=False,missing_target=False):
        scene=selected_fixtures.scene
        commands=[]
        class Source:
            fps=sensor_fps=30.
            count=0
            @property
            def latest(self):
                self.count+=1
                f=scene(); f.id=self.count
                if missing_target: f.depth[:]=0
                f.timestamp=f.received=time.monotonic()
                if self.count>=(3 if delay else 24): engine.stop.set()
                return f
        class Hand:
            last_ms=0.
            def detect(self,image): return []
            def close(self): pass
        class Finger:
            def __init__(self,*a):
                self.boxes=[]; self.hand_regions=[]
                self.uv=None if lost else np.array([90.,120.])
                self.landmarks=None if lost else np.array([[45.+i,80.+i*2] for i in range(21)])
                self.measurement=None if estimated or lost else SimpleNamespace(xyz=np.array([-.1,0,.3]),sigma=.01)
                self.state='LOST' if lost else 'SIMULATED'
                self.depth_reason='no visible fingertip' if lost else 'shape-only estimate: insufficient current palm LiDAR' if estimated else 'SIMULATED'
                self.metric=SimpleNamespace(diagnostics={})
                self.estimated_xyz=np.array([-.1,0,.3]) if estimated and not lost else None
                self.identity=1; self.confidence=1.
            def update(self,*a): time.sleep(max(.003,delay))
        class Detector:
            last_ms=320.
            def __init__(self,*a): self.calls=0
            def detect(self,image,target):
                self.calls+=1
                if self.calls>1: time.sleep(.001)
                return [(np.array([110.,65,160,165]),.8)]
        cfg=json.loads((ROOT/'config.json').read_text()); cfg['rotation']=0
        cfg['detection_mode']='manual'
        sink=SimpleNamespace(status='SIMULATED',update=lambda *a,**kw:commands.append(a))
        with tempfile.TemporaryDirectory(dir=ROOT/'logs',prefix='offline-paired-') as tmp:
            with patch('deskguide.engine.ROOT',Path(tmp)),patch('deskguide.engine.HandDetector',Hand),patch('deskguide.engine.FingerTracker',Finger),patch('deskguide.engine.ObjectDetector',Detector):
                engine=Engine(cfg,sink,source=Source()); engine.run()
                self.assertEqual(engine.error,'')
                self.assertIsNotNone(engine.result['object_box'])
                self.assertIn(engine.result['metrics']['object_result_state'],('detected','pending','tracking_only'))
                logged=[json.loads(x) for x in engine.log_path.read_text().splitlines()]
                self.assertTrue(any(x['object_result_state']=='detected' for x in logged))
                return engine.result,commands

    def test_slow_current_hand_frame_cannot_play_stale_guidance(self):
        result,commands=self.run_paired(.32)
        self.assertEqual(result['metrics']['guidance_reason'],'stale_frame')
        self.assertFalse(result['metrics']['metric_valid'])
        self.assertTrue(all(len(args)==0 for args in commands))

    def test_fresh_paired_xyz_reaches_guidance_without_manual_selection(self):
        result,commands=self.run_paired(0)
        self.assertEqual(result['metrics']['guidance_reason'],'measured')
        self.assertTrue(result['metrics']['metric_valid'])
        self.assertTrue(any(len(args)==2 for args in commands))

    def test_size_only_distance_and_21_landmarks_reach_gui_without_guidance(self):
        result,commands=self.run_paired(0,estimated=True)
        m=result['metrics']
        self.assertEqual(m['distance_kind'],'ESTIMATED')
        self.assertIsNotNone(m['display_distance_m'])
        self.assertIsNone(m['distance_m'])
        self.assertFalse(m['metric_valid'])
        self.assertEqual(result['hand_landmarks'].shape,(21,2))
        self.assertIn('guidance stopped',m['distance_reason'])
        self.assertTrue(all(len(args)==0 for args in commands))

    def test_lost_hand_clears_landmarks_and_explains_missing_distance(self):
        result,commands=self.run_paired(0,lost=True)
        self.assertIsNone(result['hand_landmarks'])
        self.assertIsNone(result['finger_uv'])
        self.assertIsNone(result['metrics']['display_distance_m'])
        self.assertEqual(result['metrics']['distance_reason'],'no visible fingertip')
        self.assertTrue(all(len(args)==0 for args in commands))

    def test_missing_target_depth_is_explained_and_cannot_guide(self):
        result,commands=self.run_paired(0,missing_target=True)
        self.assertIsNone(result['metrics']['display_distance_m'])
        self.assertIn('target surface depth',result['metrics']['distance_reason'])
        self.assertTrue(all(len(args)==0 for args in commands))

    def test_landmark_rendering_does_not_require_lidar_or_a_fresh_guidance_command(self):
        from deskguide.app import draw_hand_observation
        image=np.zeros((240,320,3),np.uint8)
        points=np.array([[45.+i,80.+i*2] for i in range(21)])
        draw_hand_observation(image,points,points[8],stale=True)
        self.assertEqual(np.count_nonzero(image),0)
        draw_hand_observation(image,points,points[8],stale=False)
        self.assertGreater(np.count_nonzero(image),100)

    def test_gui_labels_receive_distance_and_estimated_source(self):
        from deskguide.app import Window
        class Label:
            def __init__(self): self.values={}
            def config(self,**kw): self.values.update(kw)
        for estimated in (False,True):
            result,_=self.run_paired(0,estimated=estimated)
            f=result['frame']; f.received=time.monotonic(); f.relative_lag=0.
            window=Window.__new__(Window)
            window.closed=False
            window.config={'max_frame_age_ms':300}
            window.engine=SimpleNamespace(result=result,error='',completed=False,phone=SimpleNamespace(status='SIMULATED'))
            window.guidance=GuidanceOutput()
            for name in ('message','status_chip','direction','distance','directions','states','performance'):
                setattr(window,name,Label())
            window.camera=SimpleNamespace(winfo_width=lambda:320,winfo_height=lambda:240)
            window.root=SimpleNamespace(after=lambda *a:None)
            window.last_render=(f.epoch,f.id,320,240,False,False,False,not estimated)
            window.tick()
            self.assertIn('cm',window.distance.values['text'])
            self.assertIn('SIDE',window.directions.values['text'])
            self.assertIn('REACH',window.directions.values['text'])
            self.assertIn('ESTIMATED' if estimated else 'LIDAR',window.states.values['text'])

    def test_checked_flow_keeps_can_across_280ms_detector_miss(self):
        import cv2
        scene=selected_fixtures.scene
        cfg=json.loads((ROOT/'config.json').read_text())
        tracker=ObjectTracker(None,cfg)
        f=scene(); f.pose=None
        gray=cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY)
        finger=SimpleNamespace(boxes=[])
        box=np.array([110.,65,160,165])
        for now in (1.,1.06):
            tracker.update(f,gray,finger,now,prefetched=[(box,.8)])
        self.assertIsNotNone(tracker.measurement)
        tracker.update(f,gray,finger,1.34,prefetched=[])
        self.assertEqual(tracker.state,'TRACKED')
        self.assertIsNotNone(tracker.measurement)
        self.assertEqual(tracker.diagnostics['flow'],'supported')
        self.assertAlmostEqual(tracker.last_neural,1.06)
        f.depth[:]=0
        tracker.update(f,gray,finger,1.40,prefetched=[])
        self.assertIsNone(tracker.measurement)
        tracker.update(f,np.zeros_like(gray),finger,1.45,prefetched=[])
        self.assertEqual(tracker.state,'SEARCHING')
        self.assertIsNone(tracker.box)

    def test_detector_logs_other_classes_without_selecting_them(self):
        class Values:
            def __init__(self,values): self.values=np.asarray(values)
            def cpu(self): return self
            def numpy(self): return self.values
        detector=ObjectDetector.__new__(ObjectDetector)
        detector.cpu_threads=8; detector.threshold=.2
        detector.torch=SimpleNamespace(set_num_threads=lambda n:None)
        boxes=SimpleNamespace(xyxy=Values([[0,0,20,40],[10,10,30,50]]),conf=Values([.9,.4]),cls=Values([39,41]))
        detector.model=SimpleNamespace(predict=lambda *a,**k:[SimpleNamespace(boxes=boxes)])
        selected=detector.detect(np.zeros((60,60,3),np.uint8),'can')
        self.assertEqual(len(selected),1)
        self.assertEqual(len(detector.diagnostics['raw_post_nms']),2)
        self.assertEqual(detector.diagnostics['requested_class'],41)

    def run_blocked_detector(self,unsupported=False,reconnect=False,lose_after_lock=False):
        import threading
        scene=selected_fixtures.scene
        clock=[100.]
        release=threading.Event()
        commands=[]
        class Source:
            fps=sensor_fps=60.
            count=0
            @property
            def latest(self):
                self.count+=1; clock[0]+=.02
                f=scene(); f.id=self.count; f.received=f.timestamp=clock[0]
                f.depth[:]=1.5 if self.count==1 else .55
                if (unsupported and self.count>1) or (lose_after_lock and self.count>=25):
                    f.color[:]=0
                if reconnect and self.count>=10: f.epoch=2
                if self.count>=18: release.set()
                if self.count>=35: engine.stop.set()
                return f
        class Hand:
            last_ms=0.
            def detect(self,image): return []
            def close(self): pass
        class Finger:
            def __init__(self,*a):
                self.boxes=[]; self.hand_regions=[]; self.uv=np.array([90.,120.])
                self.landmarks=None; self.estimated_xyz=None
                self.measurement=SimpleNamespace(xyz=np.array([-.1,0,.3]),sigma=.01)
                self.state='SIMULATED'; self.depth_reason='SIMULATED'
                self.metric=SimpleNamespace(diagnostics={}); self.identity=1; self.confidence=1.
            def update(self,*a): time.sleep(.001)
            def reset(self): pass
        class Detector:
            last_ms=340.
            def __init__(self,*a): self.calls=0
            def detect(self,image,target):
                self.calls+=1
                if self.calls==1: return []
                if self.calls==2:
                    if not release.wait(1.): raise RuntimeError('Frame loop blocked waiting for YOLO')
                    return [(np.array([110.,65,160,165]),.8)]
                return []
        cfg=json.loads((ROOT/'config.json').read_text()); cfg['rotation']=0
        sink=SimpleNamespace(status='SIMULATED',update=lambda *a,**k:commands.append(a))
        with tempfile.TemporaryDirectory(dir=ROOT/'logs',prefix='offline-async-') as tmp:
            with patch('deskguide.engine.ROOT',Path(tmp)),patch('deskguide.engine.HandDetector',Hand),patch('deskguide.engine.FingerTracker',Finger),patch('deskguide.engine.ObjectDetector',Detector),patch('deskguide.engine.time.monotonic',side_effect=lambda:clock[0]):
                engine=Engine(cfg,sink,source=Source()); engine.run()
                self.assertEqual(engine.error,'')
                records=[json.loads(x) for x in engine.log_path.read_text().splitlines()]
                self.assertTrue(any(x['detector_pending'] for x in records[:10]))
                return engine.result,records,commands

    def test_pending_yolo_does_not_block_fresh_geometry_or_reuse_old_depth(self):
        result,records,commands=self.run_blocked_detector()
        accepted=[x for x in records if x['object_result_state']=='detected']
        self.assertTrue(accepted)
        self.assertGreater(accepted[0]['object_result_age_ms'],300)
        self.assertGreater(len(records),20)
        ends=[r['arrival_monotonic']+r['receive_age_ms']/1000 for r in records]
        self.assertLess(max(b-a for a,b in zip(ends,ends[1:])),.04)
        self.assertTrue(result['metrics']['metric_valid'])
        self.assertAlmostEqual(result['metrics']['delta_m'][2],.25,places=5)
        self.assertGreater(result['metrics']['object_details']['neural_identity_age_ms'],300)
        self.assertTrue(any(len(a)==2 for a in commands))

    def test_delayed_detection_without_current_visual_support_cannot_guide(self):
        result,records,commands=self.run_blocked_detector(unsupported=True)
        self.assertTrue(any(x['object_result_state']=='current_visual_support_rejected' for x in records))
        self.assertIsNone(result['object_box'])
        self.assertFalse(result['metrics']['metric_valid'])
        self.assertTrue(all(len(a)==0 for a in commands))

    def test_reconnect_invalidates_inflight_detection(self):
        result,records,commands=self.run_blocked_detector(reconnect=True)
        self.assertTrue(any(x['object_result_state']=='discarded_generation' for x in records))
        self.assertIsNone(result['object_box'])
        self.assertTrue(all(len(a)==0 for a in commands))

    def test_predicted_anchor_after_visual_loss_stops_guidance(self):
        result,records,commands=self.run_blocked_detector(lose_after_lock=True)
        self.assertTrue(any(len(a)==2 for a in commands))
        self.assertEqual(result['metrics']['object_state'],'PREDICTED')
        self.assertFalse(result['metrics']['metric_valid'])
        self.assertEqual(commands[-1],())

    def test_source_hand_pixels_cannot_seed_a_late_detection(self):
        import cv2
        from deskguide.flow import align_detections
        f=selected_fixtures.scene()
        gray=cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY)
        box=np.array([110.,65,160,165])
        detections=[(box,.8)]
        self.assertEqual(len(align_detections(gray,gray.copy(),detections)),1)
        region=np.array([[105,60],[165,60],[165,170],[105,170]])
        self.assertEqual(align_detections(gray,gray.copy(),detections,[region]),[])
