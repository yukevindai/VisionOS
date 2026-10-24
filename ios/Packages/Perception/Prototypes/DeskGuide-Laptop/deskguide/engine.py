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
from .contracts import FrameSource,AudioSink
from .guidance_core import evaluate


class Engine:
    def __init__(self,config,audio: AudioSink,preview=False,source: FrameSource | None=None):
        self.config=dict(config)
        self.audio=audio
        self.preview=preview
        self.phone=source if source is not None else Phone(config['host'],config['port'],usb=config.get('usb',False))
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True,name='deskguide-inference')
        self.result=None
        self.error=''
        self.ready=False
        self.reset_event=threading.Event()
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
        self.audio.update()

    def mark_appearance(self):
        self.appearance=time.monotonic()

    def rotate(self):
        self.config['rotation']=(self.config['rotation']+90)%360
        self.reset_event.set()
        self.audio.update()

    def run(self):
        cv2.setNumThreads(self.config['opencv_threads'])
        hand_model=None
        hand_worker=None
        try:
            if not self.preview:
                hand_model=HandDetector()
                finger=FingerTracker(hand_model,self.config)
                object_model=ObjectDetector(self.config['object_score'])
                obj=ObjectTracker(object_model,self.config)
                hand_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='hand-inference')
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
                        self.audio.update()
                        continue
                    f=prepare(raw,self.config['max_width'],self.config['rotation'])
                    if not self.preview:
                        if self.reset_event.is_set():
                            obj.reset()
                            finger.reset()
                            self.reset_event.clear()
                        # A discontinuous pose or lost pose quality invalidates persistent coordinates.
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
                        now=time.monotonic()
                        # Parallel model calls on the laptop; object depth still waits for
                        # current hand masks, so foreground cannot replace the anchor.
                        hand_future=hand_worker.submit(finger.update,f,gray,now)
                        detected=None
                        lost=obj.state not in ('VISIBLE','TRACKED')
                        if obj.schedule.due(now,lost):
                            detect_start=time.monotonic()
                            detected=object_model.detect(f.color,self.config['target'])
                            obj.schedule.done(detect_start,time.monotonic())
                        hand_future.result()
                        obj.update(f,gray,finger,time.monotonic(),prefetched=detected)
                    end=time.monotonic()
                    age=end-f.received+f.relative_lag
                    delta=distance=sigma=None
                    decision=evaluate(
                        None if self.preview or obj.measurement is None or finger.measurement is None else obj.contact(finger.measurement.xyz,f.K),
                        None if self.preview or finger.measurement is None else finger.measurement.xyz,
                        'PREVIEW' if self.preview else obj.state,age,limit,completed=self.completed)
                    trustworthy=decision.distance_m is not None
                    if trustworthy:
                        delta=np.array(decision.delta_m)
                        distance=decision.distance_m
                        sigma=float(np.hypot(obj.measurement.sigma,finger.measurement.sigma))
                    if trustworthy and not self.completed:
                        self.audio.update(delta,distance,deadline=f.received+limit-f.relative_lag)
                    else:
                        self.audio.update()
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
                                 backlog_growth_ms=f.relative_lag*1000,capture_latency_ms=None,
                                 receive_fps=self.phone.fps,processing_fps=fps,cpu_percent=cpu,rss_mb=rss,
                                 delta_m=None if delta is None else delta.tolist(),distance_m=distance,
                                 depth_spread_proxy_m=sigma,metric_valid=bool(trustworthy),
                                 guidance_reason=decision.reason,
                                 object_state='PREVIEW' if self.preview else obj.state,
                                 finger_state='OFF' if self.preview else finger.state,
                                 finger_depth_reason=None if self.preview else finger.depth_reason,
                                 finger_estimated=self.preview is False and finger.estimated_xyz is not None,
                                 confidence_high_share=None if f.confidence is None else float(np.mean(f.confidence>=2)),
                                 finger_id=None if self.preview else finger.identity,
                                 flow_quality=None if self.preview else finger.confidence,
                                 object_score=None if self.preview else obj.score,
                                 object_inference_ms=0 if self.preview else object_model.last_ms,
                                 hand_inference_ms=0 if self.preview else hand_model.last_ms,
                                 search_to_detection_ms=None if self.preview else obj.first_ms,
                                 confirmation_gap_ms=None if self.preview else obj.reacquire_ms,
                                 marked_acquisition_ms=acquisition,audio_state=self.audio.status)
                    self.result=dict(frame=f,metrics=metrics,
                                     object_box=None if self.preview or obj.box is None else obj.box.copy(),
                                     finger_uv=None if self.preview or finger.uv is None else finger.uv.copy(),
                                     surface_uv=None if self.preview or obj.uv is None else obj.uv.copy())
                    log.write(json.dumps(metrics)+'\n')
                    if count%15==0:
                        log.flush()
        except Exception as exc:
            self.error=f'{type(exc).__name__}: {exc}'
            self.audio.update()
        finally:
            if hand_worker:
                hand_worker.shutdown(wait=True)
            if hand_model:
                hand_model.close()

    def close(self):
        self.stop.set()
        self.audio.update()
        self.phone.close()
        self.thread.join(timeout=5)
