"""Optical coordinates x=right, y=down, z=forward, distances in meters."""
from dataclasses import dataclass
import numpy as np

AXES = np.diag([1., -1., -1., 1.])


@dataclass
class Measurement:
    xyz: np.ndarray
    sigma: float
    support: int
    source: str


def world(point, pose):
    return (pose @ AXES @ np.r_[point, 1.])[:3]


def camera(point, pose):
    return (AXES @ np.linalg.inv(pose) @ np.r_[point, 1.])[:3]


def project(point, K):
    if not np.isfinite(point).all() or point[2] <= .1:
        return None
    q = K @ point
    return q[:2]/q[2]


def backproject(uv, z, K):
    return np.linalg.solve(K, [*uv, 1.])*z


def depth_at(frame, uv, radius=1):
    dh, dw = frame.depth.shape
    h, w = frame.color.shape[:2]
    x, y = np.floor(np.asarray(uv)*[dw/w, dh/h]).astype(int)
    if not (radius <= x < dw-radius and radius <= y < dh-radius):
        return None
    region = np.s_[y-radius:y+radius+1,x-radius:x+radius+1]
    patch = frame.depth[region]
    valid = np.isfinite(patch) & (patch > .12) & (patch < 4.)
    if frame.confidence is not None:
        valid &= frame.confidence[region] >= 1
    values = patch[valid]
    if len(values) < max(5, int(patch.size*.65)) or np.ptp(values) > .04:
        return None
    median = float(np.median(values))
    sigma = max(.012, float(1.4826*np.median(abs(values-median))))
    return Measurement(backproject(uv,median,frame.K), sigma, len(values),
                       'LiDAR confidence >=1' if frame.confidence is not None else 'LiDAR confidence unavailable')


def surface(frame, box):
    """Nearest depth population of meaningful support, in the object's inner ROI.

    This is an independently implemented weighted interval-density calculation;
    repeated upsampled cells are not independent accuracy observations.
    """
    box=np.asarray(box,float)
    center=(box[:2]+box[2:])/2
    h,w=frame.color.shape[:2]
    dh,dw=frame.depth.shape
    for divisor,share,least in [(6.,.12,5),(2.2,.10,40)]:
        half=np.maximum(2.,(box[2:]-box[:2])/divisor)
        low=np.maximum(0,np.floor((center-half)*[dw/w,dh/h]).astype(int))
        high=np.minimum([dw,dh],np.floor((center+half+1)*[dw/w,dh/h]).astype(int))
        patch=frame.depth[low[1]:high[1],low[0]:high[0]]
        valid=np.isfinite(patch)&(patch>.15)&(patch<4.)
        if frame.confidence is not None:
            valid &= frame.confidence[low[1]:high[1],low[0]:high[0]]>=1
        values=patch[valid]
        if len(values)<(max(5,.3*patch.size) if divisor==6. else 30):
            continue
        levels,multiplicity=np.unique(values.astype(float),return_counts=True)
        prefix=np.r_[0,np.cumsum(multiplicity)]
        upper=levels+np.maximum(.03,.06*levels)
        ends=np.searchsorted(levels,upper,side='right')
        density=prefix[ends]-prefix[:-1]
        supported=np.flatnonzero(density>=max(least,share*len(values)))
        selected=int(supported[0]) if len(supported) else int(np.argmax(density))
        cluster=values[(values>=levels[selected])&(values<=upper[selected])]
        z=float(np.median(cluster))
        return center,Measurement(backproject(center,z,frame.K),.025,len(cluster),'LiDAR front-surface population')
    return None


def fingertip_depth(frame,uv):
    """A thin tip may cover <5 depth cells. Require a valid center and support.

    Neighbor values are accepted only around the measured center's depth; this
    does not pick an arbitrary nearest background/foreground mode.
    """
    dh,dw=frame.depth.shape
    h,w=frame.color.shape[:2]
    x,y=np.floor(np.asarray(uv)*[dw/w,dh/h]).astype(int)
    if not (1<=x<dw-1 and 1<=y<dh-1):
        return None
    center=float(frame.depth[y,x])
    if not np.isfinite(center) or not .12<center<4:
        return None
    patch=frame.depth[y-1:y+2,x-1:x+2]
    mask=np.isfinite(patch)&(abs(patch-center)<.018)
    if frame.confidence is not None:
        if frame.confidence[y,x]<1:
            return None
        mask &= frame.confidence[y-1:y+2,x-1:x+2]>=1
    values=patch[mask]
    if len(values)<3:
        return None
    z=float(np.median(values))
    return Measurement(backproject(uv,z,frame.K),.02,len(values),'LiDAR center-supported tip')


def palm_reference(frame,landmarks):
    """Robust measured hand-depth reference, not a claimed fingertip measurement."""
    palm=landmarks[[0,5,9,13,17]]
    center=np.mean(palm,axis=0)
    locations=[center]+[(center+p)/2 for p in palm]
    measured=[depth_at(frame,p) for p in locations]
    measured=[m for m in measured if m is not None]
    if len(measured)<3:
        return None
    zs=np.array([m.xyz[2] for m in measured])
    median=float(np.median(zs))
    if np.count_nonzero(abs(zs-median)<.04)<3:
        return None
    return median


def overlap(a,b,padding=12):
    return min(a[2],b[2]+padding)>max(a[0],b[0]-padding) and min(a[3],b[3]+padding)>max(a[1],b[1]-padding)


def instructions(delta):
    pairs = [('RIGHT','LEFT'),('DOWN','UP'),('FORWARD','BACK')]
    return [p[0 if v>0 else 1] if abs(v)>.025 else 'ALIGNED' for v,p in zip(delta,pairs)]
