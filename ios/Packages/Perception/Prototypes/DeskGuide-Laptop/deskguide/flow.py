import cv2
import numpy as np


class Flow:
    """Sparse LK with photometric, bounds, and forward/backward checks."""
    def __init__(self):
        self.gray = self.points = None
        self.quality = 0.

    def seed(self,gray,points):
        self.gray = gray  # Frames are immutable; avoid a redundant image copy.
        self.points = np.array(points,np.float32).reshape(-1,1,2)

    def update(self,gray):
        if self.gray is None or self.points is None or len(self.points)==0:
            return None
        if self.gray.shape != gray.shape:
            self.__init__()
            return None
        nxt,status,error = cv2.calcOpticalFlowPyrLK(self.gray,gray,self.points,None,winSize=(17,17),maxLevel=2)
        if nxt is None:
            self.__init__()
            return None
        back,reverse,_ = cv2.calcOpticalFlowPyrLK(gray,self.gray,nxt,None,winSize=(17,17),maxLevel=2)
        if back is None:
            self.__init__()
            return None
        good = status.ravel().astype(bool)&reverse.ravel().astype(bool)
        good &= np.linalg.norm(back-self.points,axis=2).ravel()<1.
        good &= error.ravel()<20
        good &= np.isfinite(nxt).all(axis=(1,2))
        good &= (nxt[:,0,0]>=0)&(nxt[:,0,0]<gray.shape[1])&(nxt[:,0,1]>=0)&(nxt[:,0,1]<gray.shape[0])
        self.quality = float(good.mean())
        old,new = self.points[good,0],nxt[good,0]
        if len(new):
            self.seed(gray,new)
            return old,new
        self.__init__()
        return None


def object_motion(old,new):
    if len(old)<6:
        return None
    affine,inliers = cv2.estimateAffinePartial2D(old,new,method=cv2.RANSAC,ransacReprojThreshold=2.)
    if affine is None or inliers.mean()<.65:
        return None
    scale = np.linalg.norm(affine[:,0])
    if not .85<scale<1.18:
        return None
    return affine


def align_detections(source_gray,current_gray,detections,source_hand_regions=()):
    """Bring asynchronous boxes onto current pixels using checked LK motion.

    Failed motion is discarded, never treated as a current detection. Geometry
    will subsequently sample ONLY the current paired depth frame.
    """
    if source_gray is current_gray:
        return detections
    if source_gray.shape!=current_gray.shape:
        return []
    h,w=current_gray.shape
    aligned=[]
    excluded=np.zeros_like(source_gray)
    for region in source_hand_regions:
        points=np.asarray(region,float)
        points=points[np.isfinite(points).all(axis=1)]
        if len(points)>=3:
            cv2.fillConvexPoly(excluded,cv2.convexHull(np.rint(points).astype(np.int32)),255)
    if np.any(excluded):
        excluded=cv2.dilate(excluded,np.ones((9,9),np.uint8))
    for box,score in detections:
        x1,y1,x2,y2=np.rint(box).astype(int)
        mask=np.zeros_like(source_gray)
        mask[max(0,y1):min(h,y2),max(0,x1):min(w,x2)]=255
        mask[excluded!=0]=0
        points=cv2.goodFeaturesToTrack(source_gray,50,.025,5,mask=mask)
        if points is None:
            continue
        flow=Flow()
        flow.seed(source_gray,points)
        tracked=flow.update(current_gray)
        affine=None if tracked is None else object_motion(*tracked)
        if affine is None:
            continue
        corners=np.array([[box[0],box[1]],[box[2],box[1]],
                          [box[2],box[3]],[box[0],box[3]]])
        mapped=np.c_[corners,np.ones(4)]@affine.T
        moved=np.clip(np.r_[mapped.min(0),mapped.max(0)],[0,0,0,0],[w-1,h-1,w-1,h-1])
        if np.min(moved[2:]-moved[:2])>=8:
            aligned.append((moved,float(score)))
    return aligned
