"""Capture a short real PathOn sample; no synthetic inputs or Proprio imports."""
import argparse
import json
import socket
import time
from pathlib import Path
import cv2
import numpy as np
from deskguide.stream import read_frame,prepare
from deskguide.models import ObjectDetector,HandDetector


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--host',required=True)
    p.add_argument('--port',type=int,default=8888)
    p.add_argument('--frames',type=int,default=30)
    a=p.parse_args()
    frames=[]
    with socket.create_connection((a.host,a.port),timeout=5) as sock:
        sock.settimeout(5)
        for _ in range(a.frames):
            frames.append(read_frame(sock))
    f=frames[-1]
    folder=Path(__file__).parent/'logs'
    folder.mkdir(exist_ok=True)
    prefix=folder/('real-sample-'+str(time.time_ns()))
    np.savez_compressed(str(prefix)+'.npz',color=f.color,depth=f.depth,K=f.K,
                        pose=f.pose,confidence=f.confidence,timestamp=f.timestamp)
    cv2.imwrite(str(prefix)+'.jpg',f.color)
    valid=f.depth[np.isfinite(f.depth)&(f.depth>.1)&(f.depth<5)]
    report=dict(rgb_shape=list(f.color.shape),depth_shape=list(f.depth.shape),K=f.K.tolist(),
                depth_valid_share=float(len(valid)/f.depth.size),depth_m_percentiles=np.percentile(valid,[10,50,90]).tolist() if len(valid) else [],
                confidence=None if f.confidence is None else dict(zip(*[v.tolist() for v in np.unique(f.confidence,return_counts=True)])),
                pose_valid=f.pose is not None,sensor_fps=(len(frames)-1)/(frames[-1].timestamp-frames[0].timestamp),
                received_fps=(len(frames)-1)/(frames[-1].received-frames[0].received),rotations=[])
    cv2.setNumThreads(2)
    objects=ObjectDetector(.25)
    for rotation in [0,90,180,270]:
        image=prepare(f,640,rotation).color
        hands=HandDetector()
        observed=[]
        # Replay the captured frame to exercise cold start and edge recovery.
        # These repetitions are not additional live observations.
        for _ in range(3):
            attempt=hands.detect(image)
            if attempt:
                observed=attempt
        detected=objects.detect(image,'cup')
        report['rotations'].append(dict(rotation=rotation,hands=len(observed),cup_scores=[s for _,s in detected],
                                       object_ms=objects.last_ms,hand_ms=hands.last_ms))
        hands.close()
    (Path(str(prefix)+'.json')).write_text(json.dumps(report,indent=2))
    print('Saved actual frame:',str(prefix)+'.jpg')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
