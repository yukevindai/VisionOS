import argparse
import json
import time
from pathlib import Path
import cv2
import numpy as np
from .paths import ROOT
from .configuration import load_config
from .guidance import GuidanceOutput
from .engine import Engine


def arguments():
    p=argparse.ArgumentParser(description='Reflex hand guidance')
    p.add_argument('--config',type=Path,default=ROOT/'config.json')
    p.add_argument('--host')
    p.add_argument('--port',type=int)
    p.add_argument('--target',choices=['can'])
    p.add_argument('--rotation',type=int,choices=[0,90,180,270])
    p.add_argument('--object-ms',type=float)
    p.add_argument('--hand-ms',type=float)
    p.add_argument('--preview',action='store_true')
    p.add_argument('--usb',action='store_true',help='Connect through Apple Mobile Device USB service, no Wi-Fi')
    p.add_argument('--usb-check',action='store_true',help='Check Apple USB service and attached iPhone')
    p.add_argument('--check',action='store_true')
    p.add_argument('--headless',action='store_true')
    p.add_argument('--seconds',type=float,default=30)
    return p.parse_args()


def main():
    args=arguments()
    config=load_config(args)
    network_run=not (args.usb_check or args.check)
    if network_run and not config.get('usb',False) and not config.get('host'):
        print('Connect the iPhone to the same network as this laptop and start PathOn. '
              'Pass --host with its CURRENT displayed IP, or run launch.ps1 to enter it. '
              'No saved venue IP will be used.')
        raise SystemExit(2)
    if args.usb_check:
        from .usb import devices
        try:
            count=len(devices())
            print(f'Apple USB service reachable. USB Apple devices: {count}')
            if count==0:
                print('Connect iPhone with a data cable, unlock, tap Trust, and confirm it appears in Apple Devices.')
                raise SystemExit(2)
        except (OSError,ValueError) as exc:
            print(str(exc))
            raise SystemExit(2)
    elif args.check:
        from .models import ObjectDetector,HandDetector
        import platform
        cv2.setNumThreads(config['opencv_threads'])
        hand=HandDetector()
        blank=np.zeros((480,640,3),np.uint8)
        print('Python',platform.python_version(),'OpenCV',cv2.__version__)
        detector=ObjectDetector(config['object_score'],config.get('torch_threads',8))
        print('Automatic target: COCO cup / class 41.')
        print('CPU object detections on blank input:',len(detector.detect(blank,'can')))
        print('Hand detections on blank input:',len(hand.detect(blank)))
        hand.close()
        print('Model initialization and execution passed. No live hardware validated.')
    elif args.headless:
        guidance=GuidanceOutput()
        engine=Engine(config,guidance,args.preview)
        engine.start()
        deadline=time.monotonic()+args.seconds
        try:
            while time.monotonic()<deadline and not engine.error:
                time.sleep(.1)
        finally:
            engine.close()
        print(json.dumps(dict(error=engine.error,phone=engine.phone.status,log=str(engine.log_path),guidance=guidance.current().direction,
                              last_metrics=None if engine.result is None else engine.result['metrics']),indent=2))
        if engine.error:
            raise SystemExit(1)
        if engine.result is None:
            print('No live frames received; phone connection is not validated.')
            raise SystemExit(2)
    else:
        from .app import Window
        window=Window(config,args.preview)
        window.root.mainloop()
