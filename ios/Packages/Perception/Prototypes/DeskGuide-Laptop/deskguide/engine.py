import json
from pathlib import Path
import threading
import time
import cv2
import numpy as np
import psutil
from concurrent.futures import ThreadPoolExecutor
from .stream import Phone,prepare
from .models import ObjectDetector,HandDetector,ROOT
from .tracking import FingerTracker,ObjectTracker
from .contracts import FrameSource,GuidanceSink
from .guidance_core import evaluate
from .geometry import project
from .flow import align_detections


class Engine:
    def __init__(self,config,guidance: GuidanceSink,preview=False,source: FrameSource | None=None):
        self.config=dict(config)
        self.guidance=guidance
        self.preview=preview
        self.phone=source if source is not None else Phone(config['host'],config['port'],usb=config.get('usb',False))
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True,name='reflex-inference')
        self.result=None
        self.error=''
        self.ready=False
        self.reset_event=threading.Event()
        self.output_lock=threading.Lock()
        self.completed=False
        self.appearance=None
        self.log_path=None

    def start(self):
        self.phone.start()
        self.thread.start()

    def reset(self,target=None):
        if target:
            self.config['target']=target
        self.completed=False
        self.reset_event.set()
        self.guidance.update()

    def mark_appearance(self):
        self.appearance=time.monotonic()

    def pause(self):
        with self.output_lock:
            self.completed=True
            self.reset_event.set()
            self.guidance.update()

    def rotate(self):
        self.config['rotation']=(self.config['rotation']+90)%360
        self.reset_event.set()
        self.guidance.update()

    def run(self):
        cv2.setNumThreads(self.config['opencv_threads'])
        hand_model=None
        detector_worker=None
        pending=None
        generation=0
        try:
            if not self.preview:
                hand_model=HandDetector()
                finger=FingerTracker(hand_model,self.config)
                object_model=ObjectDetector(self.config['object_score'],self.config.get('torch_threads',8))
                obj=ObjectTracker(object_model,self.config)
                detector_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='object-inference')
                if detector_worker:
                    detector_worker.submit(object_model.detect,np.zeros((640,640,3),np.uint8),self.config['target']).result()
            self.ready=True
            logs=ROOT/'logs'
            logs.mkdir(exist_ok=True)
            self.log_path=logs/('session-'+str(time.time_ns())+'.jsonl')
            previous=None
            previous_pose=None
            previous_sensor=None
            previous_epoch=None
            pose_generation=0
            count=0
            window=time.monotonic()
            fps=0.
            proc=psutil.Process()
            proc.cpu_percent()
            resource_t=0.
            cpu=rss=0.
            with self.log_path.open('w',encoding='utf8') as log:
                while not self.stop.is_set():
                    raw=self.phone.latest
                    if raw is None or (raw.epoch,raw.id)==previous:
                        self.stop.wait(.004)
                        continue
                    previous=(raw.epoch,raw.id)
                    start=time.monotonic()
                    limit=self.config['max_frame_age_ms']/1000
                    if start-raw.received+raw.relative_lag>limit:
                        self.guidance.update()
                        continue
                    f=prepare(raw,self.config['max_width'],self.config['rotation'])
                    prepared=time.monotonic()
                    object_result_age=None
                    object_result_state='pending'
                    detection_time=None
                    completed_detection=None
                    detector_submitted=False
                    if not self.preview:
                        if self.reset_event.is_set():
                            generation+=1
                            obj.reset()
                            finger.reset()
                            self.reset_event.clear()
                        if previous_epoch==raw.epoch and previous_sensor is not None:
                            dt=f.timestamp-previous_sensor
                            changed=(previous_pose is None)!=(f.pose is None)
                            if f.pose is not None and previous_pose is not None and 0<dt<.5:
                                displacement=np.linalg.norm(f.pose[:3,3]-previous_pose[:3,3])
                                angle=np.arccos(np.clip((np.trace(previous_pose[:3,:3].T@f.pose[:3,:3])-1)/2,-1,1))
                                changed|=displacement>max(.15,dt*3.) or angle>max(.4,dt*5.)
                            if changed:
                                pose_generation+=1
                        previous_pose=f.pose
                        previous_sensor=f.timestamp
                        previous_epoch=raw.epoch
                        f.epoch=raw.epoch*1000000+pose_generation
                        gray=cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY)
                        now=f.received
                        detected=[]
                        if pending is not None:
                            obsolete=(pending[3]!=generation or pending[1].epoch!=f.epoch
                                      or pending[4]!=self.config['target'])
                            if obsolete and pending[0].cancel():
                                pending=None
                        def collect_detection():
                            nonlocal pending,object_result_age,object_result_state,detection_time,completed_detection
                            if pending is None or not pending[0].done():
                                return []
                            future,source,source_gray,job_generation,job_target,submitted,source_hands=pending
                            pending=None
                            boxes=future.result()
                            collected=time.monotonic()
                            obj.schedule.done(submitted,collected)
                            object_result_age=collected-source.received+source.relative_lag
                            completed_detection=dict(getattr(object_model,'diagnostics',{}))
                            completed_detection.update(source_frame=source.id,source_epoch=source.epoch,
                                semantic_age_ms=object_result_age*1000)
                            if (job_generation!=generation or source.epoch!=f.epoch
                                    or job_target!=self.config['target']):
                                object_result_state='discarded_generation'
                                return []
                            if not 0<=object_result_age<self.config.get('object_identity_age_s',.9):
                                object_result_state='discarded_identity_expired'
                                return []
                            mapped=align_detections(source_gray,gray,boxes,source_hands)
                            object_result_state=('detected' if mapped else 'no_detection' if not boxes
                                                 else 'current_visual_support_rejected')
                            if mapped:
                                detection_time=source.received-source.relative_lag
                            return mapped
                        detected=collect_detection()
                        lost=obj.state not in ('VISIBLE','TRACKED')
                        if pending is None and obj.schedule.due(time.monotonic(),lost):
                            detector_started=time.monotonic()
                            pending=(detector_worker.submit(object_model.detect,f.color,self.config['target']),
                                     f,gray,generation,self.config['target'],detector_started,[])
                            detector_submitted=True
                        finger.update(f,gray,now)
                        hand_done=time.monotonic()
                        if pending is not None and pending[1] is f:
                            pending=(*pending[:-1],[p.copy() for p in getattr(finger,'hand_regions',[])])
                        if completed_detection is None:
                            detected=collect_detection()
                        if completed_detection is None:
                            object_result_state='pending' if pending is not None else 'tracking_only'
                        obj.update(f,gray,finger,now,prefetched=detected,detection_time=detection_time)
                        if completed_detection is None:
                            obj.diagnostics['selection']='detector_pending' if pending is not None else 'detector_not_due'
                    end=time.monotonic()
                    age=end-f.received+f.relative_lag
                    delta=distance=sigma=None
                    contact=None if self.preview or obj.measurement is None or finger.measurement is None else obj.contact(finger.measurement.xyz,f.K)
                    decision=evaluate(
                        contact,
                        None if self.preview or finger.measurement is None else finger.measurement.xyz,
                        'PREVIEW' if self.preview else obj.state,age,limit,completed=self.completed,
                        task_generation=generation,
                        evidence_generation=generation if (not self.reset_event.is_set()
                            and getattr(self.phone,'connection_epoch',raw.epoch)==raw.epoch) else generation-1)
                    trustworthy=decision.distance_m is not None
                    if trustworthy:
                        delta=np.array(decision.delta_m)
                        distance=decision.distance_m
                        sigma=float(np.hypot(obj.measurement.sigma,finger.measurement.sigma))
                    display=decision
                    distance_kind='LIDAR-FUSED' if trustworthy else 'UNAVAILABLE'
                    distance_reason=decision.reason
                    if not self.preview:
                        if (not trustworthy and finger.measurement is None
                                and finger.uv is not None and finger.estimated_xyz is not None):
                            display=evaluate(obj.contact(finger.estimated_xyz,f.K),
                                finger.estimated_xyz,obj.state,age,limit,
                                completed=self.completed,task_generation=generation,
                                evidence_generation=generation if (not self.reset_event.is_set()
                                    and getattr(self.phone,'connection_epoch',raw.epoch)==raw.epoch) else generation-1)
                            if display.distance_m is not None:
                                distance_kind='ESTIMATED'
                                distance_reason=finger.depth_reason+'; guidance stopped'
                        if display.distance_m is None:
                            if decision.reason=='stale_frame':
                                distance_reason=f'Frame age {age*1000:.0f} ms exceeds {limit*1000:.0f} ms; guidance stopped'
                            elif decision.reason in ('completed','task_generation_mismatch'):
                                distance_reason=decision.reason.replace('_',' ')
                            elif finger.uv is None:
                                distance_reason=finger.depth_reason
                            elif obj.measurement is None:
                                distance_reason=('Target: '+obj.state.replace('_',' ')+'; '+getattr(obj,'depth_reason','no accepted current target depth'))
                            else:
                                distance_reason=finger.depth_reason
                    with self.output_lock:
                        if trustworthy and not self.completed:
                            self.guidance.update(delta,distance,deadline=f.received+limit-f.relative_lag)
                        else:
                            self.guidance.update()
                    dispatched=time.monotonic()
                    count+=1
                    if end-window>=1:
                        fps=count/(end-window)
                        count=0
                        window=end
                    if end-resource_t>=1:
                        cpu=proc.cpu_percent()/max(1,psutil.cpu_count())
                        rss=proc.memory_info().rss/1024**2
                        resource_t=end
                    acquisition=None
                    if self.appearance is not None and not self.preview and obj.state in ('VISIBLE','TRACKED','VERIFYING_DEPTH','VERIFYING_MOVE'):
                        acquisition=(end-self.appearance)*1000
                        self.appearance=None
                    metrics=dict(frame=raw.id,epoch=raw.epoch,target=self.config['target'],
                                 rotation=self.config['rotation'],sensor_fps=self.phone.sensor_fps,
                                 image_dark=bool(np.mean(f.color[::16,::16])<8),
                                 processing_ms=(end-start)*1000,receive_age_ms=(end-f.received)*1000,
                                 sensor_timestamp=f.timestamp,arrival_monotonic=f.received,
                                 queue_wait_ms=(start-f.received)*1000,decode_ms=f.decode_ms,
                                 prepare_ms=(prepared-start)*1000,
                                 guidance_dispatch_ms=(dispatched-end)*1000,
                                 hand_stage_ms=0 if self.preview else (hand_done-prepared)*1000,
                                 object_depth_stage_ms=0 if self.preview else (end-hand_done)*1000,
                                 object_result_age_ms=None if object_result_age is None else object_result_age*1000,
                                 object_result_state=object_result_state,
                                 detector_candidates=0 if self.preview else len(detected),
                                 detector_ran=detector_submitted,
                                 detector_pending=pending is not None,
                                 detector_details=completed_detection,
                                 object_details=None if self.preview else obj.diagnostics,
                                 prepared_shape=list(f.color.shape),pose_valid=f.pose is not None,
                                 task_generation=generation,pose_generation=pose_generation,
                                 inference_schedule='single_pending_detection_current_rgbd_tracking',
                                 backlog_growth_ms=f.relative_lag*1000,capture_latency_ms=None,
                                 receive_fps=self.phone.fps,processing_fps=fps,cpu_percent=cpu,rss_mb=rss,
                                 delta_m=None if delta is None else delta.tolist(),distance_m=distance,
                                 display_distance_m=display.distance_m,display_delta_m=display.delta_m,
                                 distance_kind=distance_kind,distance_reason=distance_reason,
                                 hand_depth_source='none' if self.preview else ('palm LiDAR + hand shape' if finger.measurement is not None else 'hand size/shape estimate' if finger.estimated_xyz is not None else 'none'),
                                 hand_model_score=None if self.preview else getattr(finger,'model_score',None),
                                 hand_backend='preview' if self.preview else 'MediaPipe CPU Hand Landmarker',
                                 depth_spread_proxy_m=sigma,metric_valid=bool(trustworthy),
                                 guidance_reason=decision.reason,
                                 object_state='PREVIEW' if self.preview else obj.state,
                                 finger_state='OFF' if self.preview else finger.state,
                                 finger_depth_reason=None if self.preview else finger.depth_reason,
                                 palm_depth_details=None if self.preview else finger.metric.diagnostics,
                                 finger_estimated=self.preview is False and finger.estimated_xyz is not None,
                                 confidence_high_share=None if f.confidence is None else float(np.mean(f.confidence>=2)),
                                 confidence_usable_share=None if f.confidence is None else float(np.mean(f.confidence>=1)),
                                 finger_id=None if self.preview else finger.identity,
                                 flow_quality=None if self.preview else finger.confidence,
                                 object_score=None if self.preview else obj.score,
                                 object_inference_ms=0 if self.preview else object_model.last_ms,
                                 hand_inference_ms=0 if self.preview else hand_model.last_ms,
                                 search_to_detection_ms=None if self.preview else obj.first_ms,
                                 confirmation_gap_ms=None if self.preview else obj.reacquire_ms,
                                 marked_acquisition_ms=acquisition,guidance_direction=self.guidance.status)
                    self.result=dict(frame=f,metrics=metrics,
                                     published_monotonic=time.monotonic(),
                                     object_box=None if self.preview or obj.box is None else obj.box.copy(),
                                     finger_uv=None if self.preview or finger.uv is None else finger.uv.copy(),
                                     hand_landmarks=None if self.preview or finger.uv is None or getattr(finger,'landmarks',None) is None else finger.landmarks.copy(),
                                     surface_uv=project(contact,f.K) if trustworthy else None)
                    log.write(json.dumps(metrics)+'\n')
                    if count%15==0:
                        log.flush()
        except Exception as exc:
            self.error=f'{type(exc).__name__}: {exc}'
            self.guidance.update()
        finally:
            if detector_worker:
                detector_worker.shutdown(wait=True,cancel_futures=True)
            if hand_model:
                hand_model.close()

    def close(self):
        self.stop.set()
        self.guidance.update()
        self.phone.close()
        self.thread.join(timeout=5)
