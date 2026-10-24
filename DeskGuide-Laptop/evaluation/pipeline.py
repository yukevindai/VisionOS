"""Read-only baseline execution / independent prototype execution in separate processes.

This is diagnostic orchestration, not shipped or imported into the feature.
"""
import argparse
from dataclasses import asdict
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
BASE=Path(r'C:\Users\ngkho\OneDrive\Desktop\testing_ver_calhack')
sys.path.insert(0,str(Path(__file__).parent))
from compare import stage,inputs


def vector(x):
    return None if x is None else np.asarray(x).tolist()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--worker',required=True,choices=['baseline','prototype'])
    p.add_argument('--input-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    os.environ.update(PERCEPTION_DEVICE='cpu',PERCEPTION_DETECTOR='coco',PERCEPTION_HELPER='off',
                      HAND_OPTION='2',OBJECT_OPTION='2',MODELS_DIR=str(BASE/'upstream'/'models'),
                      YOLO_CONFIG_DIR=str(ROOT/'logs'/'evaluation-yolo-cache'))
    files=sorted(a.input_dir.glob('*.npz'))
    rows=[]
    if a.worker=='baseline':
        sys.path.insert(0,str(BASE/'upstream'))
        import perception
        from interfaces import Frame
        pipeline=perception.build()
        metadata=dict(config=asdict(pipeline.cfg),detector=type(pipeline.detector).__name__,
                      tracker=pipeline.hands.name,tracker_pad=getattr(pipeline.hands,'pad',None),
                      model=str(pipeline.detector.model.ckpt_path),imgsz=pipeline.detector.imgsz,
                      detector_threshold=pipeline.detector.conf)
    else:
        sys.path.insert(0,str(ROOT))
        from deskguide.models import ObjectDetector,HandDetector
        from deskguide.tracking import ObjectTracker,FingerTracker
        from deskguide.stream import Frame
        from deskguide.geometry import AXES
        config=json.loads((ROOT/'config.json').read_text())
        object_model=ObjectDetector(config['object_score'])
        hand_model=HandDetector()
        obj=ObjectTracker(object_model,config)
        finger=FingerTracker(hand_model,config)
        metadata=dict(config=config,detector=type(object_model).__name__)
    metadata['packages']={name:importlib.metadata.version(name) for name in ['numpy','mediapipe']}
    start_sensor=inputs(files[0])[-1]
    origin=time.monotonic()
    for i,path in enumerate(files):
        prepared=stage(path,a.worker)
        ts=origin+inputs(path)[-1]-start_sensor
        tick=time.perf_counter()
        if a.worker=='baseline':
            frame=Frame(prepared['rgb'],prepared['depth'],prepared['K'],prepared['pose'],ts,t_recv=ts)
            scene=pipeline.locate(frame,'cup')
            detail=scene.hand
            row=dict(hand_visible=bool(scene.hand_visible),object_visible=bool(scene.object_visible),
                     hand_xyz=vector(scene.hand_xyz),object_xyz=vector(scene.object_xyz),aim_xyz=vector(scene.aim_xyz),
                     object_box=vector(scene.object_box),hand_uv=vector(scene.hand_px),
                     depth_source=getattr(detail,'depth_from','none'),coasting=getattr(detail,'coasting',False),
                     keypoints=vector(getattr(detail,'keypoints_px',None)))
        else:
            color,depth,K,pose,confidence,source_ts=inputs(path)
            from deskguide.stream import prepare
            frame=prepare(Frame(i,source_ts,ts,color,depth,K,pose,confidence,1),config['max_width'],config['rotation'])
            gray=cv2.cvtColor(frame.color,cv2.COLOR_BGR2GRAY)
            finger.schedule.last=obj.schedule.last=-float('inf')
            finger.update(frame,gray,ts)
            obj.update(frame,gray,finger,ts)
            row=dict(hand_visible=finger.uv is not None,object_visible=obj.state in ('VISIBLE','TRACKED','VERIFYING_DEPTH','VERIFYING_MOVE'),
                     hand_xyz=vector(None if finger.measurement is None else finger.measurement.xyz),
                     object_xyz=vector(None if obj.measurement is None else obj.measurement.xyz),
                     aim_xyz=vector(None if obj.measurement is None or finger.measurement is None else obj.contact(finger.measurement.xyz,frame.K)),
                     object_box=vector(obj.box),hand_uv=vector(finger.uv),depth_source=finger.depth_reason,
                     finger_state=finger.state,object_state=obj.state,keypoints=vector(finger.landmarks))
        row.update(sample=path.name,timestamp=ts-origin,processing_ms=(time.perf_counter()-tick)*1000)
        rows.append(row)
    if a.worker=='baseline':
        pipeline.hands.close()
        if pipeline._hand_pool:
            pipeline._hand_pool.shutdown(wait=True)
    else:
        hand_model.close()
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(dict(metadata=metadata,rows=rows),indent=2))
    print(json.dumps(dict(worker=a.worker,frames=len(rows),hand_visible=sum(r['hand_visible'] for r in rows),
                          object_visible=sum(r['object_visible'] for r in rows),
                          hand_3d=sum(r['hand_xyz'] is not None for r in rows),
                          object_3d=sum(r['object_xyz'] is not None for r in rows),
                          processing_ms_p50_p95=np.percentile([r['processing_ms'] for r in rows],[50,95]).tolist()),indent=2))


if __name__=='__main__': main()
