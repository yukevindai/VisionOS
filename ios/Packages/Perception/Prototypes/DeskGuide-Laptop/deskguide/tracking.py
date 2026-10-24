"""Independent evidence-driven fingertip and object lifecycles."""
import time
import cv2
import numpy as np
from collections import deque
from .filters import OneEuro,Cadence
from .flow import Flow,object_motion
from .geometry import camera,world,project,backproject,depth_at,surface,overlap,fingertip_depth,palm_reference
from .metric_hand import HandDepthFusion


class FingerTracker:
    def __init__(self,detector,config):
        self.detector=detector
        self.schedule=Cadence(config['hand_interval_ms'],config['reacquire_interval_ms'])
        self.flow=Flow()
        self.smooth=OneEuro(3.,.06)
        self.uv=self.raw_uv=self.measurement=None
        self.wrist=None
        self.label=None
        self.boxes=[]
        self.state='LOST'
        self.last_neural=-1e9
        self.last_depth=None
        self.last_depth_t=-1e9
        self.confidence=0.
        self.identity=0
        self.epoch=None
        self.landmarks=None
        self.estimated_xyz=None
        self.depth_reason='no visible fingertip'
        self.metric=HandDepthFusion()

    def reset(self):
        self.flow=Flow()
        self.smooth=OneEuro(3.,.06)
        self.uv=self.raw_uv=self.measurement=self.wrist=self.label=None
        self.boxes=[]
        self.last_neural=-1e9
        self.last_depth=None
        self.last_depth_t=-1e9
        self.state='LOST'
        self.landmarks=None
        self.estimated_xyz=None
        self.metric=HandDepthFusion()

    def update(self,f,gray,now):
        if self.epoch!=f.epoch:
            self.reset()
            self.epoch=f.epoch
        tracked=self.flow.update(gray)
        old_uv=self.raw_uv
        self.raw_uv=self.uv=self.measurement=None
        self.state='LOST'
        self.confidence=0.
        self.estimated_xyz=None
        self.depth_reason='no visible fingertip'
        if tracked is not None and old_uv is not None and now-self.last_neural<.22:
            old,new=tracked
            motion=np.median(new-old,axis=0)
            residual=np.linalg.norm((new-old)-motion,axis=1)
            if len(new)>=3 and np.median(residual)<1.5:
                uv=old_uv+motion
                if 0<=uv[0]<gray.shape[1] and 0<=uv[1]<gray.shape[0]:
                    self.raw_uv=uv
                    if self.landmarks is not None:
                        self.landmarks=self.landmarks+motion
                    self.state='TRACKED_2D'
                    self.confidence=self.flow.quality
                    if self.wrist is not None:
                        self.wrist=self.wrist+motion
                    self.boxes=[b+np.tile(motion,2) for b in self.boxes]
        if self.schedule.due(now,self.raw_uv is None):
            start=time.monotonic()
            hands=self.detector.detect(f.color)
            self.schedule.done(start,time.monotonic())
            self.boxes=[h['box'] for h in hands]
            candidates=[]
            for hand in hands:
                p=hand['points']
                if not np.isfinite(p).all() or not (0<=p[8,0]<gray.shape[1] and 0<=p[8,1]<gray.shape[0]):
                    continue
                if self.wrist is not None and now-self.last_neural<.7:
                    wrist_distance=np.linalg.norm(p[0]-self.wrist)/gray.shape[1]
                    if wrist_distance>.22:
                        continue
                    identity_penalty=.06 if hand['label']!=self.label else 0
                    cost=wrist_distance+identity_penalty
                    if old_uv is not None:
                        cost+=.2*np.linalg.norm(p[8]-old_uv)/gray.shape[1]
                else:
                    cost=-hand['score']
                candidates.append((cost,hand))
            if candidates:
                hand=min(candidates,key=lambda item:item[0])[1]
                p=hand['points']
                new_identity=self.wrist is None or now-self.last_neural>=.7
                if new_identity:
                    self.identity+=1
                    self.smooth=OneEuro(3.,.06)
                    self.last_depth=None
                    self.metric=HandDepthFusion()
                self.raw_uv=p[8].copy()
                self.landmarks=p.copy()
                self.wrist=p[0].copy()
                self.label=hand['label']
                self.last_neural=now
                self.state='DETECTED_2D'
                # Seed local texture around the observed fingertip, not the full hand.
                x,y=np.rint(p[8]).astype(int)
                mask=np.zeros_like(gray)
                mask[max(0,y-16):y+17,max(0,x-16):x+17]=255
                features=cv2.goodFeaturesToTrack(gray,12,.015,3,mask=mask)
                if features is not None:
                    self.flow.seed(gray,features)
                else:
                    self.flow=Flow()
                self.confidence=1. # detector accepted; not a calibrated probability
        if self.raw_uv is not None:
            self.uv=self.smooth.update(self.raw_uv,f.timestamp)
            if self.landmarks is not None:
                self.measurement,self.estimated_xyz,self.depth_reason=self.metric.resolve(f,self.landmarks,now)
                if self.measurement is not None:
                    self.state='LIDAR_FUSED_3D / '+self.state
                elif self.estimated_xyz is not None:
                    self.state='ESTIMATED_3D / '+self.state
        elif now-self.last_neural>.25:
            self.boxes=[]
        # No extrapolated fingertip and no geometry-model depth masquerading as LiDAR.
        return self


class ObjectTracker:
    def __init__(self,detector,config):
        self.detector=detector
        self.config=config
        self.schedule=Cadence(config['object_interval_ms'],config['reacquire_interval_ms'])
        self.flow=Flow()
        self.reset()

    def reset(self):
        self.box=self.world=self.world_corners=self.uv=self.measurement=None
        self.state='SEARCHING'
        self.score=0.
        self.last_neural=self.last_anchor=-1e9
        self.candidate=None
        self.candidate_count=0
        self.epoch=None
        self.identity=0
        self.started=time.monotonic()
        self.first_ms=self.reacquire_ms=None
        self.missing_since=None
        self.flow=Flow()
        self.schedule.last=-1e9
        self.identity_lost_since=None
        self.anchor_observations=deque(maxlen=3)

    def contact(self,finger_xyz,K):
        if self.measurement is None or self.box is None:
            return None
        point=self.measurement.xyz.copy()
        half=.35*(self.box[2:]-self.box[:2])*point[2]/[K[0,0],K[1,1]]
        point[:2]+=np.clip(np.asarray(finger_xyz)[:2]-point[:2],-half,half)
        return point

    def projection(self,f,now):
        if self.world is None or f.pose is None or now-self.last_anchor>self.config['max_anchor_age_s']:
            return None
        projected=project(camera(self.world,f.pose),f.K)
        corners=[project(camera(p,f.pose),f.K) for p in self.world_corners]
        if projected is None or any(p is None for p in corners):
            return None
        points=np.array(corners)
        return projected,np.r_[points.min(0),points.max(0)]

    def observe(self,f,box,score,now):
        measured=surface(f,box)
        self.box=box.copy()
        self.score=score
        self.state='VISIBLE'
        self.last_neural=now
        self.uv=box[:2]+(box[2:]-box[:2])*[.5,.65]
        if measured is None:
            self.candidate_count=0
            return
        uv,m=measured
        self.uv=uv
        point=world(m.xyz,f.pose) if f.pose is not None else m.xyz
        space='world' if f.pose is not None else 'camera'
        candidate=(point,space)
        same=self.candidate is not None and self.candidate[1]==space and np.linalg.norm(point-self.candidate[0])<.035
        self.candidate_count=self.candidate_count+1 if same else 1
        self.candidate=candidate
        expected=camera(self.world,f.pose) if self.world is not None and f.pose is not None else None
        moved=expected is not None and np.linalg.norm(expected-m.xyz)>.045
        needed=3 if moved else 2
        if self.candidate_count<needed:
            self.state='VERIFYING_MOVE' if moved else 'VERIFYING_DEPTH'
            return
        self.measurement=m
        if f.pose is not None:
            if self.anchor_observations and np.linalg.norm(point-self.anchor_observations[-1])>.15:
                self.anchor_observations.clear()
            self.anchor_observations.append(point.copy())
            self.world=np.mean(self.anchor_observations,axis=0)
            self.measurement.xyz=camera(self.world,f.pose)
            pixels=[[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]]
            self.world_corners=[world(backproject(p,m.xyz[2],f.K),f.pose) for p in pixels]
            self.last_anchor=now
        if not self.identity:
            self.identity=1

    def update(self,f,gray,finger,now,prefetched=None):
        if self.epoch!=f.epoch:
            self.reset()
            self.epoch=f.epoch
        prev_box=None if self.box is None else self.box.copy()
        previous_uv=None if self.uv is None else self.uv.copy()
        self.measurement=None
        self.box=self.uv=None
        self.state='SEARCHING'
        projected=self.projection(f,now)
        if projected is not None:
            self.uv,self.box=projected
            self.state='PREDICTED'
        flow_result=self.flow.update(gray)
        if flow_result is not None and prev_box is not None and now-self.last_neural<.25:
            affine=object_motion(*flow_result)
            if affine is not None:
                pixels=np.array([[prev_box[0],prev_box[1]],[prev_box[2],prev_box[1]],
                                 [prev_box[2],prev_box[3]],[prev_box[0],prev_box[3]]])
                pts=np.c_[pixels,np.ones(4)]@affine.T
                self.box=np.r_[pts.min(0),pts.max(0)]
                if previous_uv is not None:
                    self.uv=affine@np.r_[previous_uv,1.]
                self.state='TRACKED'
                if self.uv is not None and not any(overlap(self.box,b) for b in finger.boxes):
                    m=depth_at(f,self.uv)
                    if m is not None and self.world is not None and f.pose is not None:
                        expected=camera(self.world,f.pose)
                        if np.linalg.norm(m.xyz-expected)<.04:
                            self.measurement=m
        covered=self.box is not None and any(overlap(self.box,b) for b in finger.boxes)
        if covered:
            self.state='OCCLUDED' if self.world is not None else 'UNCERTAIN'
            self.measurement=None
        lost=self.state not in ('VISIBLE','TRACKED') or now-self.last_neural>.25
        if prefetched is not None or self.schedule.due(now,lost):
            if prefetched is not None:
                detections=prefetched
            else:
                start=time.monotonic()
                detections=self.detector.detect(f.color,self.config['target'])
                self.schedule.done(start,time.monotonic())
            saw_candidates=bool(detections)
            reference=self.box
            if reference is not None:
                center=(reference[:2]+reference[2:])/2
                gate=max(70.,np.linalg.norm(reference[2:]-reference[:2]))
                ranked=sorted(detections,key=lambda item:np.linalg.norm((item[0][:2]+item[0][2:])/2-center))
                detections=[item for item in ranked if np.linalg.norm((item[0][:2]+item[0][2:])/2-center)<gate]
                # An expired/unassociated identity is explicitly released before broad search.
                if not detections and saw_candidates and not covered:
                    if self.identity_lost_since is None:
                        self.identity_lost_since=now
                    elif now-self.identity_lost_since>1.:
                        self.world=self.world_corners=None
                        self.box=self.uv=None
                        self.candidate=None
                        self.candidate_count=0
                        self.identity=0
                elif detections:
                    self.identity_lost_since=None
            else:
                detections=sorted(detections,key=lambda item:-item[1])
            if detections:
                box,score=detections[0]
                if any(overlap(box,b) for b in finger.boxes):
                    self.state='OCCLUDED' if self.world is not None else 'UNCERTAIN'
                    self.measurement=None
                else:
                    self.observe(f,box,score,now)
                    mask=np.zeros_like(gray)
                    x1,y1,x2,y2=np.rint(box).astype(int)
                    mask[max(0,y1):y2,max(0,x1):x2]=255
                    features=cv2.goodFeaturesToTrack(gray,50,.025,5,mask=mask)
                    if features is not None:
                        self.flow.seed(gray,features)
        visible=self.state in ('VISIBLE','TRACKED','VERIFYING_DEPTH','VERIFYING_MOVE')
        if visible:
            if self.first_ms is None:
                self.first_ms=(now-self.started)*1000
            if self.missing_since is not None:
                self.reacquire_ms=(now-self.missing_since)*1000
                self.missing_since=None
        elif self.missing_since is None:
            self.missing_since=now
        if self.world is not None and now-self.last_anchor>self.config['max_anchor_age_s']:
            self.world=self.world_corners=None
            self.candidate=None
            self.candidate_count=0
            if not visible:
                self.box=self.uv=None
                self.state='UNCERTAIN'
        return self
