"""Independently authored adapters for licensed CPU inference libraries."""
from pathlib import Path
import time
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT/'models'
# This specific can is observed as COCO cup (41) in the reference screenshot.
# 'can' is the user's target name, not a claim that COCO has a can category.
CLASSES = {'can':41,'cup':41,'bottle':39,'bowl':45}


class HandDetector:
    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions,vision
        self.mp = mp
        options = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS/'hand_landmarker.task'),delegate=BaseOptions.Delegate.CPU),
            running_mode=vision.RunningMode.VIDEO,num_hands=2,
            min_hand_detection_confidence=.3,min_hand_presence_confidence=.3,
            min_tracking_confidence=.3)
        self.model = vision.HandLandmarker.create_from_options(options)
        self.last_ms=0.
        self.last_timestamp=-1
        self.origin=time.monotonic()

    def detect(self,image,timestamp_ms=None):
        start = time.perf_counter()
        h,w=image.shape[:2]
        rgb = cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        # Replicated border supports hands at the edge; map pixels back afterwards.
        margin=160
        rgb=cv2.copyMakeBorder(rgb,margin,margin,margin,margin,cv2.BORDER_REPLICATE)
        timestamp=max(self.last_timestamp+1,round((time.monotonic()-self.origin)*1000) if timestamp_ms is None else int(timestamp_ms))
        self.last_timestamp=timestamp
        result=self.model.detect_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb)),timestamp)
        found=[]
        for points,handedness in zip(result.hand_landmarks,result.handedness):
            points=np.array([(p.x*(w+2*margin)-margin,p.y*(h+2*margin)-margin) for p in points])
            found.append(dict(points=points,label=handedness[0].category_name,
                              score=float(handedness[0].score),box=np.r_[points.min(0),points.max(0)]))
        self.last_ms=(time.perf_counter()-start)*1000
        return found

    def close(self):
        self.model.close()

    def detect_at(self,image,received):
        return self.detect(image,timestamp_ms=round(received*1000))


class ObjectDetector:
    """Same third-party COCO detector/settings as the audited working baseline.

    Ultralytics and its weights are AGPL-3.0; see THIRD_PARTY.md. This adapter
    is independently authored and never loads an evaluation-source module.
    """
    def __init__(self,threshold=.2,cpu_threads=8):
        import os
        os.environ.setdefault('YOLO_CONFIG_DIR',str(ROOT/'logs'/'yolo-settings'))
        from ultralytics import YOLO
        import torch
        self.torch=torch
        self.cpu_threads=max(1,int(cpu_threads))
        torch.set_num_threads(self.cpu_threads)
        path=MODELS/'yolo11s.pt'
        if not path.exists():
            raise FileNotFoundError('Run setup_models.py to fetch the licensed yolo11s.pt asset')
        self.model=YOLO(str(path))
        self.threshold=threshold
        self.last_ms=0.
        self.diagnostics={}

    def detect(self,image,target):
        start=time.perf_counter()
        # Ultralytics initializes its own CPU thread count on first predict.
        # Use the configured CPU budget, preserving the established model/input.
        self.torch.set_num_threads(self.cpu_threads)
        results=self.model.predict(image,device='cpu',imgsz=640,conf=self.threshold,classes=None,
                                   iou=.7,agnostic_nms=False,max_det=300,rect=True,half=False,quantize=None,
                                   verbose=False,save=False)[0]
        rows=[]
        raw=[]
        if results.boxes is not None:
            for coordinates,score,category in zip(results.boxes.xyxy.cpu().numpy(),results.boxes.conf.cpu().numpy(),results.boxes.cls.cpu().numpy()):
                category=int(category)
                raw.append(dict(box=coordinates.tolist(),score=float(score),class_id=category))
                if category==CLASSES[target]:
                    rows.append((coordinates.astype(float),float(score)))
        self.last_ms=(time.perf_counter()-start)*1000
        self.diagnostics=dict(input_shape=list(image.shape),imgsz=640,confidence_threshold=self.threshold,
                              requested_class=CLASSES[target],raw_post_nms=raw[:20],
                              target_boxes=[dict(box=b.tolist(),score=s) for b,s in rows],
                              result='target_found' if rows else 'no_target_class_above_threshold',
                              stage_ms=getattr(results,'speed',None))
        return sorted(rows,key=lambda row:-row[1])
