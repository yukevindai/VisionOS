import time
import cv2
import numpy as np
from collections import deque
from .filters import Cadence
from .flow import Flow, object_motion
from .geometry import camera, world, project, backproject, surface, overlap


def hand_covers(box,finger):
    if box is None:
        return False
    regions=getattr(finger,'hand_regions',None)
    if regions is None:
        return any(overlap(box,b) for b in finger.boxes)
    box=np.asarray(box,float)
    margin=.10*(box[2:]-box[:2])
    lo,hi=box[:2]-margin,box[2:]+margin
    rect=np.array([lo,[hi[0],lo[1]],hi,[lo[0],hi[1]]],np.float32)
    for points in regions:
        points=np.asarray(points,np.float32)
        points=points[np.isfinite(points).all(axis=1)]
        if len(points) and np.any(np.all((points>=lo)&(points<=hi),axis=1)):
            return True
        if len(points)>=3:
            area,_=cv2.intersectConvexConvex(cv2.convexHull(points),rect)
            if area>0:
                return True
    return False


class ObjectTracker:
    def __init__(self,detector,config):
        self.detector=detector
        self.config=config
        self.schedule=Cadence(config['object_interval_ms'],config['reacquire_interval_ms'])
        self.flow=Flow()
        self.reset()

    def reset(self):
        self.box=self.world=self.world_corners=self.uv=self.measurement=None
        self.reset_count=getattr(self,'reset_count',0)+1
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
        self.last_camera_depth=None
        self.depth_reason='no automatic detection'
        self.diagnostics={}

    def choose_detection(self,f,detections,reference):
        def iou(box):
            if reference is None:
                return 0.
            intersection=np.maximum(0.,np.minimum(box[2:],reference[2:])-np.maximum(box[:2],reference[:2]))
            area=float(np.prod(intersection))
            union=float(np.prod(box[2:]-box[:2])+np.prod(reference[2:]-reference[:2])-area)
            return area/max(union,1e-9)
        ranked=[]
        for box,score in sorted(detections,key=lambda item:-item[1])[:6]:
            measured=surface(f,box)
            reach=1 if measured is None else (2 if measured[1].xyz[2]<=1.1 else 0)
            continuity=iou(box)
            ranked.append(((reach,float(score)+.5*continuity),box,score,continuity))
        self.diagnostics['selection_candidates']=[dict(box=b.tolist(),score=float(s),reach_rank=r[0],rank_score=r[1],iou=c) for r,b,s,c in ranked]
        if not ranked:
            self.diagnostics['selection']='no_candidates'
            return []
        rank,box,score,continuity=max(ranked,key=lambda item:item[0])
        if (f.pose is None and self.last_camera_depth is not None and self.last_camera_depth<=1.1
                and rank[0]<2 and continuity==0.):
            self.diagnostics['selection']='unrelated_unknown_or_far_candidate'
            return []
        self.diagnostics['selection']='selected'
        return [(box,score)]

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

    def observe(self,f,box,score,now,neural=True,neural_time=None):
        measured=surface(f,box)
        self.box=box.copy()
        self.score=score
        self.state='VISIBLE'
        if neural:
            self.last_neural=now if neural_time is None else neural_time
        self.uv=box[:2]+(box[2:]-box[:2])*[.5,.65]
        if measured is None:
            self.candidate_count=0
            self.depth_reason='no usable target surface depth/support'
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
            self.depth_reason=f'awaiting consistent target depth ({self.candidate_count}/{needed})'
            return
        self.measurement=m
        self.depth_reason='current target surface depth accepted'
        self.last_camera_depth=float(m.xyz[2])
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

    def update(self,f,gray,finger,now,prefetched=None,detection_time=None):
        if self.epoch!=f.epoch:
            self.reset()
            self.epoch=f.epoch
        prev_box=None if self.box is None else self.box.copy()
        previous_uv=None if self.uv is None else self.uv.copy()
        self.measurement=None
        self.box=self.uv=None
        self.state='SEARCHING'
        self.depth_reason='no current supported detection/track'
        self.diagnostics=dict(reset_count=self.reset_count,flow='unsupported',selection='not_run')
        projected=self.projection(f,now)
        if projected is not None:
            self.uv,self.box=projected
            self.state='PREDICTED'
        flow_result=self.flow.update(gray)
        identity_age=now-self.last_neural
        identity_limit=self.config.get('object_identity_age_s',.9)
        if flow_result is not None and prev_box is not None and identity_age<identity_limit:
            affine=object_motion(*flow_result)
            if affine is not None:
                self.diagnostics['flow']='supported'
                pixels=np.array([[prev_box[0],prev_box[1]],[prev_box[2],prev_box[1]],
                                 [prev_box[2],prev_box[3]],[prev_box[0],prev_box[3]]])
                pts=np.c_[pixels,np.ones(4)]@affine.T
                self.box=np.r_[pts.min(0),pts.max(0)]
                if previous_uv is not None:
                    self.uv=affine@np.r_[previous_uv,1.]
                self.state='TRACKED'
        elif identity_age>=identity_limit:
            self.diagnostics['flow']='identity_confirmation_expired'
        covered=hand_covers(self.box,finger)
        if covered:
            self.state='OCCLUDED' if self.world is not None else 'UNCERTAIN'
            self.measurement=None
            self.depth_reason='hand overlaps target; target depth cannot be trusted'
        lost=self.state not in ('VISIBLE','TRACKED') or identity_age>=identity_limit
        neural_observed=False
        if prefetched is not None or self.schedule.due(now,lost):
            if prefetched is not None:
                detections=prefetched
            else:
                start=time.monotonic()
                detections=self.detector.detect(f.color,self.config['target'])
                self.schedule.done(start,time.monotonic())
            reference=self.box if self.box is not None else prev_box
            detections=self.choose_detection(f,detections,reference)
            if detections:
                neural_observed=True
                box,score=detections[0]
                if hand_covers(box,finger):
                    self.state='OCCLUDED' if self.world is not None else 'UNCERTAIN'
                    self.measurement=None
                    self.depth_reason='hand overlaps target; target depth cannot be trusted'
                else:
                    self.observe(f,box,score,now,neural_time=detection_time)
                    mask=np.zeros_like(gray)
                    x1,y1,x2,y2=np.rint(box).astype(int)
                    mask[max(0,y1):y2,max(0,x1):x2]=255
                    features=cv2.goodFeaturesToTrack(gray,50,.025,5,mask=mask)
                    if features is not None:
                        self.flow.seed(gray,features)
        if not neural_observed and self.state=='TRACKED' and not covered:
            self.observe(f,self.box,self.score,now,neural=False)
            if self.state=='VISIBLE':
                self.state='TRACKED'
        self.diagnostics.update(state=self.state,depth=self.depth_reason,
                                confirmation_count=self.candidate_count,
                                neural_identity_age_ms=max(0.,now-self.last_neural)*1000)
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
