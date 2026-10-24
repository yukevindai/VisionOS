"""Official MediaPipe CPU adapter, independently authored for this prototype."""
import time
import cv2
import numpy as np
from .paths import MODELS


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
