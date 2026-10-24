import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np
import cv2
from deskguide.stream import Frame,prepare
from deskguide.geometry import depth_at,surface,world,camera,project,instructions
from deskguide.filters import OneEuro,Cadence
from deskguide.flow import Flow,object_motion
from deskguide.tracking import FingerTracker,ObjectTracker
from deskguide.models import ROOT


def config():
    return json.loads((ROOT/'config.json').read_text())


def frame():
    rng=np.random.default_rng(5)
    return Frame(1,1.,1.,rng.integers(0,255,(480,640,3),np.uint8),np.ones((120,160),np.float32),
                 np.array([[600.,0,320],[0,600,240],[0,0,1]]),np.eye(4),None,1)


class FakeHand:
    def __init__(self):
        self.observations=[]

    def detect(self,image):
        return self.observations


def hand(x=300,label='Left'):
    points=np.tile([x,300.],(21,1))
    points[0]=[x,380]
    points[8]=[x,250]
    return dict(points=points,label=label,score=.9,box=np.r_[points.min(0),points.max(0)])


class FakeObjects:
    def __init__(self):
        self.box=np.array([200.,150,400,350])
        self.observations=True

    def detect(self,image,target):
        return [(self.box.copy(),.9)] if self.observations else []


class TrackingTests(unittest.TestCase):
    def test_2d_survives_missing_depth(self):
        d=FakeHand()
        d.observations=[hand()]
        tracker=FingerTracker(d,config())
        f=frame()
        f.depth[:]=np.nan
        tracker.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),1.)
        self.assertIsNotNone(tracker.uv)
        self.assertIsNone(tracker.measurement)
        self.assertIn('DETECTED_2D',tracker.state)

    def test_flow_between_neural_detections(self):
        d=FakeHand()
        d.observations=[hand()]
        settings=config()
        settings['hand_interval_ms']=70
        tracker=FingerTracker(d,settings)
        f=frame()
        gray=cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY)
        tracker.update(f,gray,1.)
        original=tracker.raw_uv.copy()
        f.color=cv2.warpAffine(f.color,np.float32([[1,0,3],[0,1,2]]),(640,480))
        f.timestamp+=.03
        tracker.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),1.03)
        np.testing.assert_allclose(tracker.raw_uv-original,[3,2],atol=.3)
        self.assertIn('TRACKED_2D',tracker.state)

    def test_other_hand_rejected_during_short_loss(self):
        d=FakeHand()
        d.observations=[hand(100)]
        tracker=FingerTracker(d,config())
        f=frame()
        tracker.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),1.)
        d.observations=[hand(550,'Right')]
        f.color[:]=0
        tracker.schedule.last=-1e9
        tracker.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),1.1)
        self.assertIsNone(tracker.uv)
        self.assertEqual(tracker.identity,1)

    def test_loss_and_reacquisition(self):
        d=FakeHand()
        d.observations=[hand()]
        tracker=FingerTracker(d,config())
        f=frame()
        tracker.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),1.)
        d.observations=[]
        f.color[:]=0
        tracker.schedule.last=-1e9
        tracker.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),1.3)
        self.assertEqual(tracker.state,'LOST')
        self.assertIsNone(tracker.uv)
        d.observations=[hand()]
        tracker.schedule.last=-1e9
        tracker.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),1.4)
        self.assertIsNotNone(tracker.uv)

    def test_depth_jump_keeps_2d_only(self):
        d=FakeHand()
        d.observations=[hand()]
        tracker=FingerTracker(d,config())
        f=frame()
        f.depth[:]=.5
        gray=cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY)
        tracker.update(f,gray,1.)
        self.assertIsNotNone(tracker.measurement)
        f.depth[:]=2.
        tracker.schedule.last=-1e9
        tracker.update(f,gray,1.05)
        self.assertIsNotNone(tracker.uv)
        self.assertIsNone(tracker.measurement)

    def locked_object(self):
        detector=FakeObjects()
        tracker=ObjectTracker(detector,config())
        f=frame()
        gray=cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY)
        finger=SimpleNamespace(boxes=[])
        for now in [1.,1.1]:
            tracker.schedule.last=-1e9
            tracker.update(f,gray,finger,now)
        self.assertIsNotNone(tracker.world)
        return tracker,detector,f,gray,finger

    def test_occluded_anchor_and_pan(self):
        tracker,detector,f,gray,finger=self.locked_object()
        saved=tracker.world.copy()
        oldbox=tracker.box.copy()
        tracker.flow=Flow()
        f.pose[0,3]=.1
        f.depth[:]=.4
        finger.boxes=[np.array([150,100,450,400])]
        tracker.schedule.last=-1e9
        tracker.update(f,gray,finger,1.2)
        np.testing.assert_array_equal(saved,tracker.world)
        self.assertIsNone(tracker.measurement)
        self.assertEqual(tracker.state,'OCCLUDED')
        self.assertLess(tracker.box[0],oldbox[0])

    def test_movement_requires_three_observations(self):
        tracker,detector,f,gray,finger=self.locked_object()
        saved=tracker.world.copy()
        f.depth[:]=1.15
        for i in range(2):
            tracker.schedule.last=-1e9
            tracker.update(f,gray,finger,1.2+i*.1)
            np.testing.assert_array_equal(saved,tracker.world)
            self.assertIsNone(tracker.measurement)
            self.assertEqual(tracker.state,'VERIFYING_MOVE')
        tracker.schedule.last=-1e9
        tracker.update(f,gray,finger,1.4)
        self.assertGreater(np.linalg.norm(tracker.world-saved),.04)
        self.assertIsNotNone(tracker.measurement)

    def test_occlusion_does_not_release_identity_early(self):
        tracker,detector,f,gray,finger=self.locked_object()
        saved=tracker.world.copy()
        detector.observations=False
        finger.boxes=[np.array([150,100,450,400])]
        tracker.flow=Flow()
        for now in [1.3,2.6,3.]:
            tracker.schedule.last=-1e9
            tracker.update(f,gray,finger,now)
            np.testing.assert_array_equal(saved,tracker.world)
            self.assertEqual(tracker.state,'OCCLUDED')
            self.assertIsNone(tracker.measurement)

    def test_expired_anchor_and_reconnect(self):
        tracker,detector,f,gray,finger=self.locked_object()
        detector.observations=False
        tracker.flow=Flow()
        tracker.update(f,gray,finger,20.)
        self.assertIsNone(tracker.world)
        self.assertIsNone(tracker.box)
        tracker.world=np.ones(3)
        f.epoch=2
        tracker.update(f,gray,finger,20.1)
        self.assertIsNone(tracker.world)

    def test_no_frozen_box_without_pose_or_flow(self):
        tracker,detector,f,gray,finger=self.locked_object()
        detector.observations=False
        f.pose=None
        tracker.flow=Flow()
        tracker.update(f,gray,finger,1.5)
        self.assertIsNone(tracker.box)
        self.assertIsNone(tracker.measurement)

    def test_surface_quality(self):
        f=frame()
        sampled=surface(f,np.array([200.,150,400,350]))
        self.assertIsNotNone(sampled)
        f.depth[:]=np.nan
        self.assertIsNone(surface(f,np.array([200.,150,400,350])))

    def test_rotation_preserves_world_geometry(self):
        f=frame()
        uv=np.array([300.,200.])
        p=np.linalg.solve(f.K,[*uv,1.])
        w=world(p,f.pose)
        rotated=prepare(f,1000,90)
        newuv=np.array([479-uv[1],uv[0]])
        q=np.linalg.solve(rotated.K,[*newuv,1.])
        np.testing.assert_allclose(world(q,rotated.pose),w,atol=1e-7)
        np.testing.assert_allclose(camera(w,rotated.pose),q)

    def test_filter_jitter_and_timing_governor(self):
        smooth=OneEuro()
        raw=np.array([1.,-1.]*30)
        filtered=[smooth.update([v],i/60)[0] for i,v in enumerate(raw)]
        self.assertLess(np.std(filtered[10:]),np.std(raw)/2)
        cadence=Cadence(100,35)
        cadence.done(1.,1.1)
        self.assertFalse(cadence.due(1.12,True))
        self.assertTrue(cadence.due(1.4,True))

    def test_direction_signs_and_registration(self):
        self.assertEqual(instructions([.1,-.2,.3]),['RIGHT','UP','FORWARD'])
        f=frame()
        m=depth_at(f,[320,240])
        np.testing.assert_allclose(m.xyz,[0,0,1.])
        f.confidence=np.zeros_like(f.depth,dtype=np.uint8)
        self.assertIsNone(depth_at(f,[320,240]))


if __name__=='__main__': unittest.main()
