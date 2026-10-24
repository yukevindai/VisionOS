"""Bounded real RGB-D recording for identical-input numerical evaluation."""
import argparse
from pathlib import Path
import socket
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deskguide.stream import read_frame


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--host',required=True)
    p.add_argument('--seconds',type=float,default=8)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    count=0
    with socket.create_connection((a.host,8888),timeout=5) as sock:
        sock.settimeout(5)
        start=time.monotonic()
        last=-float('inf')
        while time.monotonic()-start<a.seconds and count<60:
            f=read_frame(sock)
            if f.timestamp-last<.15:
                continue
            np.savez_compressed(a.out/f'{count:04}.npz',color=f.color,depth=f.depth,K=f.K,
                                pose=f.pose,confidence=f.confidence,timestamp=f.timestamp,
                                received=f.received,frame_id=f.id)
            last=f.timestamp
            count+=1
    print(f'Recorded {count} actual RGB-D samples to {a.out}',flush=True)
    if not count:
        raise SystemExit(2)


if __name__=='__main__': main()
