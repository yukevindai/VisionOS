import time
import cv2
import numpy as np
from .filters import OneEuro, Cadence
from .flow import Flow
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
        self.hand_regions=[]
        self.state='LOST'
        self.last_neural=-1e9
        self.last_depth=None
        self.last_depth_t=-1e9
        self.confidence=0.
        self.model_score=None
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
        self.hand_regions=[]
        self.last_neural=-1e9
        self.last_depth=None
        self.last_depth_t=-1e9
        self.state='LOST'
        self.landmarks=None
        self.model_score=None
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
        self.metric.diagnostics={}
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
                    self.hand_regions=[p+motion for p in self.hand_regions]
        if self.schedule.due(now,self.raw_uv is None):
            start=time.monotonic()
            hands=(self.detector.detect_at(f.color,f.received) if hasattr(self.detector,'detect_at')
                   else self.detector.detect(f.color))
            self.schedule.done(start,time.monotonic())
            self.boxes=[h['box'] for h in hands]
            self.hand_regions=[h['points'].copy() for h in hands]
            if not hands and self.raw_uv is not None and self.landmarks is not None:
                self.hand_regions=[self.landmarks.copy()]
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
                x,y=np.rint(p[8]).astype(int)
                mask=np.zeros_like(gray)
                mask[max(0,y-16):y+17,max(0,x-16):x+17]=255
                features=cv2.goodFeaturesToTrack(gray,12,.015,3,mask=mask)
                if features is not None:
                    self.flow.seed(gray,features)
                else:
                    self.flow=Flow()
                self.model_score=float(hand['score'])
                self.confidence=self.model_score
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
            self.hand_regions=[]
            self.model_score=None
        return self
