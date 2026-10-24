"""Isolated numerical evaluation, NOT imported by the Reflex feature.

Baseline worker runs under its existing evaluation Python, with bytecode writes
disabled. No upstream source is copied or modified. Outputs stay in this project.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
BASE=Path(r'C:\Users\ngkho\OneDrive\Desktop\testing_ver_calhack')
sys.path.insert(0,str(ROOT))


def inputs(path):
    raw=np.load(path,allow_pickle=True)
    def optional(name):
        x=raw[name]
        return None if x.shape==() and x.item() is None else x
    return raw['color'],raw['depth'],raw['K'],optional('pose'),optional('confidence'),float(raw['timestamp'])


def stage(path,which):
    color,depth,K,pose,confidence,ts=inputs(path)
    if which=='baseline':
        sys.path.insert(0,str(BASE/'upstream'))
        import stream
        raw=stream.RawFrame(cv2.cvtColor(color,cv2.COLOR_BGR2RGB),stream._clean_depth(depth,confidence),K,
                            stream._valid_pose(pose),'pathon',ts)
        f=stream.prepare(raw,t=ts)
        return dict(rgb=f.rgb,depth=f.depth,K=f.K,pose=f.pose)
    from deskguide.stream import Frame,prepare
    f=prepare(Frame(1,ts,time.monotonic(),color,depth,K,pose,confidence,1),
              json.loads((ROOT/'config.json').read_text())['max_width'],90)
    # Normalize the prototypes' raw-depth layout for comparison to RGB-aligned baseline.
    aligned=cv2.resize(f.depth,(f.color.shape[1],f.color.shape[0]),interpolation=cv2.INTER_NEAREST)
    if f.confidence is not None:
        conf=cv2.resize(f.confidence,(f.color.shape[1],f.color.shape[0]),interpolation=cv2.INTER_NEAREST)
        aligned=aligned.copy()
        aligned[conf<1]=0
    return dict(rgb=cv2.cvtColor(f.color,cv2.COLOR_BGR2RGB),depth=aligned,K=f.K,
                pose=None if f.pose is None else f.pose@np.diag([1.,-1.,-1.,1.]))


def worker(args):
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    detector=None
    first_ts=None
    if args.hand:
        if args.worker=='baseline':
            sys.path.insert(0,str(BASE/'upstream'))
            import hands
            detector=hands.make('mediapipe_wide',device='cpu',fetch=False)
        else:
            from deskguide.models import HandDetector
            detector=HandDetector()
    for path in args.inputs:
        result=stage(path,args.worker)
        np.savez_compressed(out/(Path(path).stem+'.npz'),**result)
        if args.hand:
            ts=inputs(path)[-1]
            if first_ts is None:
                first_ts=ts
            timestamp=1000+round((ts-first_ts)*1000)
            if args.worker=='baseline':
                found=detector.detect(result['rgb'],timestamp,depth=result['depth'],K=result['K'])
                rows=[dict(points=h.kp.tolist(),score=h.score) for h in found]
            else:
                found=detector.detect(cv2.cvtColor(result['rgb'],cv2.COLOR_RGB2BGR),timestamp_ms=timestamp)
                rows=[dict(points=h['points'].tolist(),score=h['score']) for h in found]
            (out/(Path(path).stem+'-hand.json')).write_text(json.dumps(rows))
    if detector:
        detector.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--worker',choices=['baseline','prototype'])
    p.add_argument('--out',default=str(ROOT/'logs'/'comparison-before'))
    p.add_argument('--inputs',nargs='*')
    p.add_argument('--hand',action='store_true')
    p.add_argument('--input-dir',type=Path)
    args=p.parse_args()
    if args.worker:
        worker(args)
        return
    paths=args.inputs or ([str(p) for p in sorted(args.input_dir.glob('*.npz'))] if args.input_dir else [str(p) for p in sorted((ROOT/'logs').glob('real-sample-*.npz'))])
    if not paths:
        raise SystemExit('No real recorded samples are available')
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PERCEPTION_DEVICE='cpu',PERCEPTION_HELPER='off',HAND_OPTION='2',OBJECT_OPTION='2')
    before={name:hashlib.sha256((BASE/'upstream'/name).read_bytes()).hexdigest() for name in ['stream.py','hands.py','perception.py','guidance.py','audio_out.py','interfaces.py']}
    for which,python in [('baseline',BASE/'.venv'/'Scripts'/'python.exe'),('prototype',Path(sys.executable))]:
        command=[str(python),'-B',str(Path(__file__).resolve()),'--worker',which,'--out',str(Path(args.out)/which),'--inputs',*paths]
        if args.hand:
            command.append('--hand')
        subprocess.run(command,check=True,env=env,cwd=ROOT)
    rows=[]
    for path in paths:
        name=Path(path).stem+'.npz'
        a=np.load(Path(args.out)/'baseline'/name,allow_pickle=True)
        b=np.load(Path(args.out)/'prototype'/name,allow_pickle=True)
        same_shape=a['rgb'].shape==b['rgb'].shape
        row=dict(sample=name,baseline_rgb=list(a['rgb'].shape),prototype_rgb=list(b['rgb'].shape),
                 baseline_depth=list(a['depth'].shape),prototype_depth=list(b['depth'].shape),
                 baseline_valid_depth_share=float(np.mean(a['depth']>0)),prototype_valid_depth_share=float(np.mean(b['depth']>0)),
                 K_max_abs=float(np.max(abs(a['K']-b['K']))),pose_max_abs=float(np.max(abs(a['pose']-b['pose']))),
                 rgb_max_abs=None if not same_shape else int(np.max(abs(a['rgb'].astype(int)-b['rgb'].astype(int)))),
                 depth_max_abs=None if not same_shape else float(np.max(abs(a['depth']-b['depth']))))
        rows.append(row)
        if args.hand:
            hand_name=Path(path).stem+'-hand.json'
            ah=json.loads((Path(args.out)/'baseline'/hand_name).read_text())
            bh=json.loads((Path(args.out)/'prototype'/hand_name).read_text())
            row['baseline_hands']=len(ah)
            row['prototype_hands']=len(bh)
            row['landmark_max_error_px']=float(np.max(abs(np.array(ah[0]['points'])-np.array(bh[0]['points'])))) if len(ah)==len(bh)==1 else None
    after={name:hashlib.sha256((BASE/'upstream'/name).read_bytes()).hexdigest() for name in before}
    report=dict(upstream_sources_unchanged=before==after,source_hashes=before,rows=rows)
    (Path(args.out)/'report.json').write_text(json.dumps(report,indent=2))
    if len(rows)>8:
        summary=dict(frames=len(rows),sources_unchanged=before==after,
                     input_max_K_error=max(r['K_max_abs'] for r in rows),
                     input_max_pose_error=max(r['pose_max_abs'] for r in rows),
                     baseline_hand_frames=sum(r.get('baseline_hands',0)>0 for r in rows),
                     prototype_hand_frames=sum(r.get('prototype_hands',0)>0 for r in rows),
                     landmark_median_error_px=float(np.median([r['landmark_max_error_px'] for r in rows if r.get('landmark_max_error_px') is not None])) if any(r.get('landmark_max_error_px') is not None for r in rows) else None)
        print(json.dumps(summary,indent=2))
    else:
        print(json.dumps(report,indent=2))


if __name__=='__main__': main()
