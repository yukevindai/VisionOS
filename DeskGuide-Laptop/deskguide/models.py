"""CPU model adapters. No import path includes the Proprio evaluation."""
import importlib.util
from pathlib import Path
import time
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT/'models'
CLASSES = {'cup':41,'bottle':39,'bowl':45}


class ArchivedNanoDet:
    def __init__(self,threshold=.4):
        spec = importlib.util.spec_from_file_location('deskguide_zoo_decoder',MODELS/'nanodet.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.decoder = mod.NanoDet(str(MODELS/'nanodet.onnx'),prob_threshold=threshold)
        self.threshold = threshold
        self.last_ms = 0.

    def detect(self,image,target):
        start = time.perf_counter()
        h,w = image.shape[:2]
        # Aspect-preserving letterbox with reversible pixel mapping.
        scale = min(416/w,416/h)
        rw,rh = round(w*scale),round(h*scale)
        dx,dy = (416-rw)//2,(416-rh)//2
        padded = np.full((416,416,3),114,np.uint8)
        padded[dy:dy+rh,dx:dx+rw] = cv2.resize(image,(rw,rh))
        decoder = self.decoder
        decoder.net.setInput(decoder.pre_process(padded))
        outputs = decoder.net.forward(decoder.net.getUnconnectedOutLayersNames())
        scores = sorted((x for x in outputs if x.shape[-1]==80),key=lambda x:-x.shape[-2])
        boxes = {x.shape[-2]:x for x in outputs if x.shape[-1]==32}
        if not scores or any(x.shape[-2] not in boxes for x in scores):
            raise ValueError('Unsupported NanoDet output tensors')
        result = decoder.post_process([v for score in scores for v in (score,boxes[score.shape[-2]])])
        found=[]
        for row in result:
            if int(row[5])!=CLASSES[target] or row[4]<self.threshold:
                continue
            box = (row[:4]-[dx,dy,dx,dy])/scale
            box = np.clip(box,[0,0,0,0],[w-1,h-1,w-1,h-1])
            if min(box[2:]-box[:2])>=8:
                found.append((box,float(row[4])))
        self.last_ms = (time.perf_counter()-start)*1000
        return found


class HandDetector:
    def __init__(self):
        import mediapipe as mp
        self.mp = mp
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODELS/'hand_landmarker.task')),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,num_hands=2,
            min_hand_detection_confidence=.3,min_hand_presence_confidence=.3,
            min_tracking_confidence=.3)
        self.model = mp.tasks.vision.HandLandmarker.create_from_options(options)
        self.last_ms=0.
        self.last_timestamp=-1
        self.origin=time.monotonic()

    def detect(self,image,timestamp_ms=None):
        start = time.perf_counter()
        h,w=image.shape[:2]
        rgb = cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        # Single audited Option-2 configuration, with inverse pixel mapping.
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


class ObjectDetector:
    """Same third-party COCO detector/settings as the audited working baseline.

    Ultralytics and its weights are AGPL-3.0; see THIRD_PARTY.md. This adapter
    is independently authored and never loads an evaluation-source module.
    """
    def __init__(self,threshold=.2):
        import os
        os.environ.setdefault('YOLO_CONFIG_DIR',str(ROOT/'logs'/'yolo-settings'))
        from ultralytics import YOLO
        path=MODELS/'yolo11s.pt'
        if not path.exists():
            raise FileNotFoundError('Run setup_models.py to fetch the licensed yolo11s.pt asset')
        self.model=YOLO(str(path))
        self.threshold=.2
        self.last_ms=0.

    def detect(self,image,target):
        start=time.perf_counter()
        results=self.model.predict(image,device='cpu',imgsz=640,conf=.2,classes=[CLASSES[target]],
                                   verbose=False,save=False)[0]
        rows=[]
        if results.boxes is not None:
            for coordinates,score in zip(results.boxes.xyxy.cpu().numpy(),results.boxes.conf.cpu().numpy()):
                rows.append((coordinates.astype(float),float(score)))
        self.last_ms=(time.perf_counter()-start)*1000
        return sorted(rows,key=lambda row:-row[1])
