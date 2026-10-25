"""Optional local open-vocabulary detector for HYBRID mode: Ultralytics YOLO-World v2 small (AGPL-3.0).

Weights are fetched by Ultralytics into backend/.runtime/models (ignored); its CLIP text encoder is cached by the
clip package. Loaded once in the background; the per-request vocabulary is the request's object words plus a few
common anchors. Boxes are in the pixels of the image passed in. Class names are generic; never brand evidence.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault('YOLO_AUTOINSTALL', 'false')

import numpy as np

MODELS = Path(__file__).resolve().parents[1] / '.runtime/models'
WEIGHTS = MODELS / 'yolov8s-worldv2.pt'
ANCHORS = ('cup', 'bottle', 'can', 'bagel', 'laptop', 'keyboard', 'plate', 'phone', 'mug', 'bowl', 'box')


def merge(candidates, overlap=.6):
    """One candidate per physical box: YOLO-World can name the same object with two classes."""
    kept = []
    for c in sorted(candidates, key=lambda c: -c['score']):
        for k in kept:
            if _iou(c['box'], k['box']) > overlap:
                k['names'].append(c['name'])
                break
        else:
            kept.append(dict(c, names=[c['name']]))
    for i, k in enumerate(kept, 1):
        k['id'] = i
    return kept


def _iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.


class LocalDetector:
    def __init__(self, weights=WEIGHTS, conf=.12, imgsz=640, background=True):
        self.weights, self.conf, self.imgsz = Path(weights), conf, imgsz
        self.model = None
        self.vocabulary = None
        self.embeddings = {}
        self.error = ''
        self.load_s = None
        self.ready = threading.Event()
        self.lock = threading.Lock()
        if background:
            threading.Thread(target=self._load, daemon=True, name='r3-local-detector').start()
        else:
            self._load()

    def _load(self):
        started = time.perf_counter()
        try:
            from ultralytics import YOLO
            self.weights.parent.mkdir(parents=True, exist_ok=True)
            model = YOLO(str(self.weights))
            self._set_classes(model, ANCHORS, reset=True)
            model.predict(np.zeros((480, 640, 3), np.uint8), imgsz=self.imgsz, conf=self.conf, verbose=False, device='cpu')
            self.model, self.vocabulary = model, tuple(ANCHORS)
            self.load_s = time.perf_counter() - started
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'[:200]
        finally:
            self.ready.set()

    def _set_classes(self, model, vocabulary, reset=False):
        """Same result as YOLOWorld.set_classes, faster: each phrase is encoded once by a cached CLIP model
        (~5 s per call without caching on this CPU) and the predictor keeps its setup instead of being rebuilt."""
        import torch
        world = model.model
        missing = [p for p in vocabulary if p not in self.embeddings]
        if missing:
            features = world.get_text_pe(missing, cache_clip_model=True)
            for i, phrase in enumerate(missing):
                self.embeddings[phrase] = features[:, i:i + 1]
        features = torch.cat([self.embeddings[p] for p in vocabulary], dim=1)
        world.txt_feats = features
        world.model[-1].nc = len(vocabulary)
        world.names = list(vocabulary)
        predictor_model = getattr(getattr(model.predictor, 'model', None), 'model', None)
        if reset or predictor_model is None or not hasattr(predictor_model, 'txt_feats'):
            model.predictor = None
        else:
            # The predictor holds its own (fused) copy of the network; it must receive the same text features.
            predictor_model.txt_feats = features
            predictor_model.model[-1].nc = len(vocabulary)
            predictor_model.names = list(vocabulary)
            model.predictor.model.names = dict(enumerate(vocabulary))

    @property
    def status(self):
        if not self.ready.is_set():
            return 'LOADING'
        return f'READY ({self.load_s:.1f}s load)' if self.model is not None else f'UNAVAILABLE ({self.error})'

    def detect(self, image, phrases):
        if self.model is None:
            return None, 0.
        vocabulary = tuple(dict.fromkeys([p for p in phrases if p] + list(ANCHORS)))
        rgb = np.asarray(image.convert('RGB'))
        with self.lock:
            started = time.perf_counter()
            if vocabulary != self.vocabulary:
                self._set_classes(self.model, vocabulary)
                self.vocabulary = vocabulary
            result = self.model.predict(rgb[..., ::-1], imgsz=self.imgsz, conf=self.conf, verbose=False, device='cpu')[0]
            ms = (time.perf_counter() - started) * 1000
            # Decode with this prediction's own vocabulary before another thread can switch it.
            names = list(vocabulary)
            found = []
            if result.boxes is not None:
                for xyxy, score, cls in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                            result.boxes.cls.cpu().numpy()):
                    if 0 <= int(cls) < len(names):
                        found.append(dict(name=names[int(cls)], score=float(score), box=tuple(map(float, xyxy))))
        return merge(found), ms
