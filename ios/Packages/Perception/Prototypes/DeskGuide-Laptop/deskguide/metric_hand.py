"""Independent geometry implementation from the audited mathematical specification.

Numerical anthropometric/calibration parameters are data, not copied source.
Palm depth is a measurement; fingertip position remains a fused estimate.
Only fresh usable LiDAR can authorize the returned Measurement. No coasting.
"""
from collections import deque
import math
import numpy as np
from .geometry import Measurement,backproject

# Audited average-palm profile, in meters; subject-specific scale is learned
# only while independent LiDAR and shape observations agree.
PALM_IDS=np.array([0,5,9,13,17])
PALM_TEMPLATE=np.array([[0.,0.],[-.022,.089],[0.,.091],[.019,.085],[.035,.074]])
SPAN_PAIRS=np.array([[0,1],[0,2],[0,3],[0,4],[1,4],[1,3],[2,4]])
INDEX_LENGTHS=np.array([.040,.025,.022])


def shape_depth(points,K):
    palm=points[PALM_IDS]
    physical=np.linalg.norm(PALM_TEMPLATE[SPAN_PAIRS[:,0]]-PALM_TEMPLATE[SPAN_PAIRS[:,1]],axis=1)
    pixels=np.linalg.norm(palm[SPAN_PAIRS[:,0]]-palm[SPAN_PAIRS[:,1]],axis=1)
    density=float(np.max(pixels/physical))
    if not np.isfinite(density) or density<=1e-6:
        return None
    nominal=float(K[0,0])/density
    projected=np.linalg.norm(np.diff(points[[5,6,7,8]],axis=0),axis=1)/density
    extension=np.sqrt(np.maximum(0.,INDEX_LENGTHS**2-(projected/.9)**2)).sum()
    palm_projection=float(np.linalg.norm(points[9]-points[0]))/density
    tilt=math.sqrt(max(0.,.091**2-(palm_projection/.9)**2))
    tip=nominal+.2*tilt+min(.10,float(extension))
    return nominal,tip/nominal


def measured_palm(frame,points,diagnostics=None):
    palm=points[PALM_IDS]
    center=palm.mean(0)
    sites=np.concatenate((center[None],center+.5*(palm-center),center+.75*(palm-center)))
    h,w=frame.color.shape[:2]
    dh,dw=frame.depth.shape
    xy=np.rint(sites).astype(int)
    inside=(xy[:,0]>=0)&(xy[:,0]<w)&(xy[:,1]>=0)&(xy[:,1]<h)
    if diagnostics is not None:
        diagnostics.update(sample_count=len(sites),in_image_count=int(inside.sum()),
                           confidence_available=frame.confidence is not None,
                           valid_count=0,raw_depth_m=[],confidence_values=[])
    if inside.sum()<4:
        return None
    xy=xy[inside]
    uv=np.floor(xy*[dw/w,dh/h]).astype(int)
    values=frame.depth[uv[:,1],uv[:,0]]
    good=np.isfinite(values)&(values>.15)&(values<4.)
    if frame.confidence is not None:
        good &= frame.confidence[uv[:,1],uv[:,0]]>=1
    if diagnostics is not None:
        diagnostics.update(valid_count=int(good.sum()),
                           raw_depth_m=[float(v) if np.isfinite(v) else None for v in values],
                           confidence_values=[] if frame.confidence is None else frame.confidence[uv[:,1],uv[:,0]].tolist())
    if good.sum()/len(sites)<.4:
        return None
    return float(np.median(values[good]))


class MetricFilter:
    """Speed-adaptive first-order low-pass filter in metric coordinates."""
    def __init__(self):
        self.state=None

    def update(self,value,t):
        value=np.asarray(value,float)
        if self.state is None or not 0<t-self.state[0]<=.5:
            self.state=(t,value.copy(),np.zeros(3))
        else:
            last,filtered,velocity=self.state
            dt=t-last
            derivative_gain=1.-1./(1.+2.*math.pi*1.5*dt)
            velocity=(1.-derivative_gain)*velocity+derivative_gain*(value-filtered)/dt
            gain=1.-1./(1.+2.*math.pi*(.8+15.*np.linalg.norm(velocity))*dt)
            self.state=(t,filtered+gain*(value-filtered),velocity)
        return self.state[1].copy()


class HandDepthFusion:
    def __init__(self):
        self.scales=deque(maxlen=90)
        self.correction=0.
        self.lidar_weight=0.
        self.last=None
        self.filter=MetricFilter()
        self.diagnostics={}

    def resolve(self,frame,points,t):
        self.diagnostics={'frame_id':frame.id if hasattr(frame,'id') else None,
                          'accepted':False}
        geometry=shape_depth(points,frame.K)
        if geometry is None:
            self.diagnostics['rejection']='invalid_shape'
            return None,None,'invalid hand geometry'
        nominal,tip_ratio=geometry
        lidar=measured_palm(frame,points,self.diagnostics)
        self.diagnostics.update(lidar_m=lidar,nominal_shape_m=nominal)
        arm=points[0]-points[9]
        if np.linalg.norm(arm)>1e-6 and arm[1]/np.linalg.norm(arm)<-.25:
            self.diagnostics['rejection']='forearm_orientation'
            return None,None,'forearm orientation rejected'
        calibration=float(np.median(self.scales)) if len(self.scales)>=10 else 1.
        size=nominal*calibration
        self.diagnostics.update(calibrated_shape_m=size,agreement_limit_m=max(.08,.3*size))
        if lidar is not None and lidar>1.5*size+.05:
            self.diagnostics['rejection']='depth_behind_shape'
            return None,None,'palm depth is behind hand geometry'
        if (lidar if lidar is not None else size)>.9:
            self.diagnostics['rejection']='beyond_0.9m'
            return None,None,'hand beyond reference reach limit'
        if lidar is not None and .25<=lidar<=.8 and .6<lidar/nominal<1.6:
            self.scales.append(lidar/nominal)
            calibration=float(np.median(self.scales)) if len(self.scales)>=10 else 1.
            size=nominal*calibration
        accepted=lidar is not None and .22<=lidar<=.9 and abs(lidar-size)<=max(.08,.3*size)
        rejection=('insufficient_valid_palm_samples' if lidar is None else
                   'outside_0.22_to_0.9m' if not .22<=lidar<=.9 else
                   'lidar_shape_disagreement' if not accepted else None)
        self.diagnostics.update(accepted=bool(accepted),rejection=rejection,
                                calibrated_shape_m=size,agreement_limit_m=max(.08,.3*size))
        fresh=self.last is None or not 0<t-self.last<=.5
        dt=1. if self.last is None else max(0.,t-self.last)
        self.last=t
        if accepted:
            gain=1. if fresh else -math.expm1(-dt/.12)
            self.correction+=gain*(lidar-size-self.correction)
            self.lidar_weight+=gain*(1.-self.lidar_weight)
        else:
            decay=0. if fresh else math.exp(-dt/.6)
            self.correction*=decay
            self.lidar_weight*=decay
        z=(size+self.correction)*tip_ratio
        point=backproject(points[8],z,frame.K)
        if not accepted:
            summary=f'{rejection}; LiDAR={lidar if lidar is None else round(lidar,3)}m, shape={size:.3f}m, support={self.diagnostics["valid_count"]}/11'
            return None,point,'shape-only estimate: no accepted current palm LiDAR; '+summary
        point=self.filter.update(point,t)
        return Measurement(point,.05,11,'palm LiDAR + hand geometry (fused estimate)'),None,'LiDAR-fused fingertip estimate'
