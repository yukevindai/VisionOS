"""Explicitly synthetic, reproducible CPU/resource microbenchmark."""
import json
import platform
import time
import cv2
import numpy as np
import psutil
from .models import ObjectDetector,HandDetector,ROOT
from .tracking import ObjectTracker,FingerTracker
from .stream import Frame


def benchmark(config,seconds):
    cv2.setNumThreads(config['opencv_threads'])
    detector=ObjectDetector(config['object_score'])
    hands=HandDetector()
    obj=ObjectTracker(detector,config)
    finger=FingerTracker(hands,config)
    image=np.zeros((480,640,3),np.uint8)
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    frame=Frame(0,0,time.monotonic(),image,np.full((120,160),np.nan,np.float32),
                np.array([[600,0,320],[0,600,240],[0,0,1.]]),np.eye(4),None,1)
    for _ in range(3):
        detector.detect(image,'cup')
        hands.detect(image)
    proc=psutil.Process()
    initial_cpu=sum(proc.cpu_times()[:2])
    initial_rss=proc.memory_info().rss
    peak_rss=initial_rss
    latencies=[]
    object_times=[]
    hand_times=[]
    start=time.monotonic()
    deadline=start+max(1.,seconds)
    calls_object=calls_hand=0
    try:
        while time.monotonic()<deadline:
            tick=time.monotonic()
            frame.timestamp=tick-start
            frame.id+=1
            previous_hand=finger.schedule.last
            previous_obj=obj.schedule.last
            finger.update(frame,gray,tick)
            obj.update(frame,gray,finger,time.monotonic())
            if finger.schedule.last!=previous_hand:
                calls_hand+=1
                hand_times.append(hands.last_ms)
            if obj.schedule.last!=previous_obj:
                calls_object+=1
                object_times.append(detector.last_ms)
            latencies.append((time.monotonic()-tick)*1000)
            peak_rss=max(peak_rss,proc.memory_info().rss)
            # A 30 Hz sensor budget, rather than burning CPU in an unbounded artificial loop.
            time.sleep(max(0.,1/30-(time.monotonic()-tick)))
    finally:
        hands.close()
    elapsed=time.monotonic()-start
    cpu=sum(proc.cpu_times()[:2])-initial_cpu
    def summary(values):
        return dict(p50_ms=float(np.percentile(values,50)),p95_ms=float(np.percentile(values,95)),max_ms=float(max(values))) if values else None
    report=dict(kind='blank-input synthetic CPU benchmark; NOT live validation',python=platform.python_version(),
                platform=platform.platform(),elapsed_s=elapsed,iterations=len(latencies),loop_fps=len(latencies)/elapsed,
                object_calls=calls_object,hand_calls=calls_hand,object=summary(object_times),hand=summary(hand_times),
                pipeline=summary(latencies),process_cpu_machine_percent=100*cpu/elapsed/max(1,psutil.cpu_count()),
                logical_cpus=psutil.cpu_count(),rss_start_mb=initial_rss/1024**2,rss_end_mb=proc.memory_info().rss/1024**2,
                rss_peak_mb=peak_rss/1024**2,config=config)
    logs=ROOT/'logs'
    logs.mkdir(exist_ok=True)
    path=logs/'benchmark.json'
    path.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return report
