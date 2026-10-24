"""Official Ultralytics CPU adapter; dependency is AGPL-3.0 (THIRD_PARTY.md)."""
import time
import numpy as np
from .paths import ROOT, MODELS

CLASSES = {'can':41,'cup':41,'bottle':39,'bowl':45}


class ObjectDetector:
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
