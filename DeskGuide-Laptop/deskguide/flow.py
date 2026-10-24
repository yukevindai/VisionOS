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
