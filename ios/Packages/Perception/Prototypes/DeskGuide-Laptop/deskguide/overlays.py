import cv2
import numpy as np


def draw_hand_observation(image,landmarks,fingertip,stale=False):
    if stale:
        return
    color=(145,225,100)
    if landmarks is not None:
        points=np.asarray(landmarks,float)
        if points.shape==(21,2) and np.isfinite(points).all():
            points=np.rint(points).astype(int)
            for chain in ((0,1,2,3,4),(0,5,6,7,8),(5,9,10,11,12),
                          (9,13,14,15,16),(13,17,18,19,20),(0,17)):
                for a,b in zip(chain,chain[1:]):
                    cv2.line(image,tuple(points[a]),tuple(points[b]),color,1,cv2.LINE_AA)
            for p in points:
                cv2.circle(image,tuple(p),2,color,-1,cv2.LINE_AA)
    if fingertip is not None and np.isfinite(fingertip).all():
        cv2.circle(image,tuple(np.rint(fingertip).astype(int)),8,(255,240,90),2,cv2.LINE_AA)
