"""CPU-only appearance tracking for one explicitly selected beverage can.

Selection supplies identity, not a class prediction or a depth measurement.
Every guiding update still needs current visual evidence and paired LiDAR.
"""
import cv2
import numpy as np
from .tracking import ObjectTracker,hand_covers
from .flow import Flow,object_motion


class SelectedCanTracker(ObjectTracker):
    def __init__(self,config):
        super().__init__(None,config)

    def reset(self):
        super().reset()
        self.template=None
        self.selected=False
        self.last_visual=None
        self.visual_score=0.
        self.state='SELECT_CAN'

    def select(self,frame,box):
        self.reset()
        h,w=frame.color.shape[:2]
        box=np.clip(np.rint(box),[0,0,0,0],[w,h,w,h]).astype(int)
        if np.min(box[2:]-box[:2])<16:
            return False
        x1,y1,x2,y2=box
        gray=cv2.cvtColor(frame.color,cv2.COLOR_BGR2GRAY)
        crop=gray[y1:y2,x1:x2]
        if np.std(crop)<4.:
            self.state='SELECT_MORE_CAN_DETAIL'
            return False
        scale=min(1.,128/max(crop.shape))
        self.template=cv2.resize(crop,(max(8,round(crop.shape[1]*scale)),max(8,round(crop.shape[0]*scale))))
        self.box=box.astype(float)
        self.epoch=frame.epoch
        self.selected=True
        self.state='VERIFYING_SELECTION'
        self.seed(gray)
        return True

    def seed(self,gray):
        mask=np.zeros_like(gray)
        x1,y1,x2,y2=np.rint(self.box).astype(int)
        mask[max(0,y1):max(0,y2),max(0,x1):max(0,x2)]=255
        points=cv2.goodFeaturesToTrack(gray,50,.025,5,mask=mask)
        self.flow=Flow()
        if points is not None:
            self.flow.seed(gray,points)

    def match(self,gray,predicted):
        """Bounded local, multiscale matching to a fixed selection template."""
        h,w=gray.shape
        size=predicted[2:]-predicted[:2]
        center=(predicted[:2]+predicted[2:])/2
        lo=np.maximum(0,np.floor(center-size).astype(int))
        hi=np.minimum([w,h],np.ceil(center+size).astype(int))
        roi=gray[lo[1]:hi[1],lo[0]:hi[0]]
        if roi.size==0:
            return None
        scale=min(1.,256/max(roi.shape))
        roi=cv2.resize(roi,(max(1,round(roi.shape[1]*scale)),max(1,round(roi.shape[0]*scale))))
        best=None
        for factor in (.94,1.,1.06):
            tw,th=np.maximum(8,np.rint(size*factor*scale)).astype(int)
            if tw>roi.shape[1] or th>roi.shape[0]:
                continue
            template=cv2.resize(self.template,(int(tw),int(th)))
            scores=cv2.matchTemplate(roi,template,cv2.TM_CCOEFF_NORMED)
            _,peak,_,location=cv2.minMaxLoc(scores)
            # A second similarly good, spatially separate match is ambiguous.
            rest=scores.copy()
            x,y=location
            rx,ry=max(2,tw//3),max(2,th//3)
            rest[max(0,y-ry):y+ry+1,max(0,x-rx):x+rx+1]=-1
            alternative=float(rest.max())
            if peak<.65 or peak-alternative<.04:
                continue
            a=lo+np.array(location)/scale
            box=np.r_[a,a+np.array([tw,th])/scale]
            if best is None or peak>best[1]:
                best=(box,float(peak))
        return best

    def update(self,f,gray,finger,now,prefetched=None):
        self.measurement=None
        if not self.selected:
            return self
        if f.epoch!=self.epoch:
            self.reset()
            return self
        # A remembered anchor can suggest where to LOOK, never authorize audio.
        projected=self.projection(f,now)
        predicted=self.box.copy() if projected is None else projected[1]
        tracked=self.flow.update(gray)
        if tracked is not None:
            affine=object_motion(*tracked)
            if affine is not None:
                a,b,c,d=self.box
                corners=np.array([[a,b],[c,b],[c,d],[a,d]])
                moved=np.c_[corners,np.ones(4)]@affine.T
                predicted=np.r_[moved.min(0),moved.max(0)]
        if hand_covers(predicted,finger):
            self.candidate_count=0
            self.state='OCCLUDED'
            return self
        if self.last_visual is not None and now-self.last_visual>.7:
            self.selected=False
            self.state='LOST_RESELECT_CAN'
            return self
        match=self.match(gray,predicted)
        if match is None:
            self.candidate_count=0
            if self.last_visual is None:
                self.last_visual=now
            self.state='UNCERTAIN'
            return self
        box,score=match
        if hand_covers(box,finger):
            self.candidate_count=0
            self.state='OCCLUDED'
            return self
        if self.last_visual is not None and now-self.last_visual>.3:
            self.candidate_count=0
            self.anchor_observations.clear()
        self.last_visual=now
        self.visual_score=score
        self.observe(f,box,score,now)
        self.seed(gray)
        if self.measurement is not None:
            self.state='TRACKED'
        elif self.state=='VISIBLE':
            self.state='NO_VALID_CAN_DEPTH'
        return self
