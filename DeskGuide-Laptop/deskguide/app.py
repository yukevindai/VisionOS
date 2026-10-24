import argparse
import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk
import cv2
import numpy as np
from PIL import Image,ImageTk
from .audio import Audio,self_test
from .engine import Engine
from .geometry import instructions
from .models import ROOT


class Window:
    def __init__(self,config,preview=False,no_audio=False):
        self.config=config
        self.root=tk.Tk()
        self.root.title('DeskGuide Laptop | Live RGB-D')
        window_width=min(1400,self.root.winfo_screenwidth()-80)
        window_height=min(900,self.root.winfo_screenheight()-100)
        self.root.geometry(f'{window_width}x{window_height}')
        self.root.minsize(min(1000,window_width),min(700,window_height))
        self.root.configure(bg='#101826')
        self.root.columnconfigure(0,weight=7)
        self.root.columnconfigure(1,weight=3,minsize=350)
        self.root.rowconfigure(0,weight=1)
        self.camera=tk.Label(self.root,bg='#070d17',fg='#b6c6df',text='Connecting to iPhone…',font=('Segoe UI',18))
        self.camera.grid(row=0,column=0,sticky='nsew')
        panel=tk.Frame(self.root,bg='#101826',padx=22,pady=16)
        panel.grid(row=0,column=1,sticky='nsew')
        def label(text,size,color='#dce6f5'):
            item=tk.Label(panel,text=text,font=('Segoe UI',size),bg='#101826',fg=color,justify='left',anchor='w',wraplength=345)
            item.pack(fill='x',pady=5)
            return item
        label('DESKGUIDE / LAPTOP',12,'#65e1cf')
        self.target_label=label(config['target'].upper(),24)
        self.distance=label('— cm',42)
        self.directions=label('Waiting for measurements',17)
        self.states=label('',11)
        self.performance=label('',10,'#9eb1cb')
        self.message=label('',10,'#e3b579')
        self.target=ttk.Combobox(panel,values=['cup','bottle','bowl'],state='readonly')
        self.target.set(config['target'])
        self.target.pack(fill='x',pady=6)
        self.target.bind('<<ComboboxSelected>>',lambda event:self.engine.reset(self.target.get()))
        controls=tk.Frame(panel,bg='#101826')
        controls.pack(fill='x',pady=4)
        controls.columnconfigure(0,weight=1)
        controls.columnconfigure(1,weight=1)
        for i,(text,action) in enumerate([('Reset [R]',self.reset),('Mute [M]',self.mute),
                            ('Complete [Enter]',self.complete),('Mark entry [V]',self.mark),
                            ('Depth [D]',self.depth),('Rotate [O]',self.rotate),('Fullscreen [F11]',self.full)]):
            ttk.Button(controls,text=text,command=action).grid(row=i//2,column=i%2,sticky='ew',padx=2,pady=3)
        label('Measured geometry only.\nNear-target cue awaits physical validation.',9,'#8299b6')
        self.audio=Audio(config['audio_volume'],config['audio_device'])
        self.audio_error=''
        if not preview and not no_audio:
            try:
                self.audio.start()
            except Exception as exc:
                self.audio_error='Audio unavailable: '+str(exc)
        self.engine=Engine(config,self.audio,preview)
        self.show_depth=False
        self.fullscreen=False
        self.last_render=None
        self.closed=False
        for key,action in [('r',self.reset),('m',self.mute),('d',self.depth),('v',self.mark),
                           ('o',self.rotate),
                           ('<F11>',self.full),('<Return>',self.complete),('<Escape>',self.escape)]:
            self.root.bind(key,lambda event,fn=action:fn())
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.engine.start()
        self.tick()

    def reset(self):
        self.engine.reset()

    def mute(self):
        self.audio.muted=not self.audio.muted

    def complete(self):
        self.engine.completed=True
        self.audio.update()
        self.audio.complete_until=time.monotonic()+.8

    def mark(self):
        self.engine.mark_appearance()

    def depth(self):
        self.show_depth=not self.show_depth
        self.last_render=None

    def full(self):
        self.fullscreen=not self.fullscreen
        self.root.attributes('-fullscreen',self.fullscreen)

    def rotate(self):
        self.engine.rotate()
        self.last_render=None

    def escape(self):
        self.fullscreen=False
        self.root.attributes('-fullscreen',False)

    def tick(self):
        if self.closed:
            return
        result=self.engine.result
        error=self.engine.error or self.audio_error
        self.message.config(text=error or self.engine.phone.status)
        if result:
            f,m=result['frame'],result['metrics']
            age=time.monotonic()-f.received+f.relative_lag
            stale=age>self.config['max_frame_age_ms']/1000
            valid=m['metric_valid'] and not stale and not self.engine.completed
            self.target_label.config(text=m['target'].upper())
            self.distance.config(text=f"≈ {m['distance_m']*100:.0f} cm" if valid else '— cm')
            if valid:
                words=instructions(m['delta_m'])
                text='\n'.join(f'{word}  {abs(value)*100:.1f} cm' for word,value in zip(words,m['delta_m']))
            elif self.engine.completed:
                text='Completion confirmed'
            else:
                text='Guidance paused\n'+('Frame stale' if stale else 'Need valid object + finger depth')
            self.directions.config(text=text)
            quality='LiDAR-fused estimate / uncalibrated' if valid else 'Metric geometry unavailable'
            self.states.config(text=f"Object: {m['object_state']}\nFinger: {m['finger_state']}\n{quality}\nAudio: {self.audio.status}")
            self.performance.config(text=f"Sensor {m.get('sensor_fps',0):.1f} · Received {m['receive_fps']:.1f} · Processed {m['processing_fps']:.1f} fps\nHost age + backlog {age*1000:.0f} ms · Pipeline {m['processing_ms']:.0f} ms\nObject / hand {m['object_inference_ms']:.0f} / {m['hand_inference_ms']:.0f} ms\nCPU {m['cpu_percent']:.1f}% · RAM {m['rss_mb']:.0f} MB\nCapture latency: unknown")
            if not error:
                if m.get('image_dark'):
                    self.message.config(text='Camera image is dark. Uncover rear camera and check PathOn preview.')
                elif m.get('confidence_high_share')==0:
                    self.message.config(text='PathOn reports no high-confidence depth. 2D tracking continues; precise audio is paused.')
                elif not valid and m.get('finger_depth_reason') and m['finger_state']!='LOST':
                    self.message.config(text=m['finger_depth_reason'])
            width,height=max(1,self.camera.winfo_width()),max(1,self.camera.winfo_height())
            key=(f.epoch,f.id,width,height,self.show_depth,stale)
            if self.last_render!=key:
                self.last_render=key
                image=f.color.copy()
                if not stale:
                    box=result['object_box']
                    if box is not None:
                        a,b,c,d=np.clip(box,[0,0,0,0],[image.shape[1]-1,image.shape[0]-1]*2).astype(int)
                        color=(100,220,100) if m['object_state'] in ('VISIBLE','TRACKED') else (40,170,240)
                        cv2.rectangle(image,(a,b),(c,d),color,2)
                        cv2.putText(image,m['object_state'],(a,max(18,b-7)),0,.5,color,1,cv2.LINE_AA)
                    if result['finger_uv'] is not None:
                        cv2.circle(image,tuple(np.rint(result['finger_uv']).astype(int)),8,(255,240,90),2,cv2.LINE_AA)
                else:
                    cv2.putText(image,'STALE - GUIDANCE PAUSED',(15,35),0,.7,(40,100,255),2)
                if self.show_depth:
                    dep=cv2.applyColorMap(np.uint8(np.clip(np.nan_to_num(f.depth)/3,0,1)*255),cv2.COLORMAP_TURBO)
                    thumb=cv2.resize(dep,(128,96))
                    if image.shape[0]>=96 and image.shape[1]>=128:
                        image[:96,:128]=thumb
                pil=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
                pil.thumbnail((width,height))
                self.photo=ImageTk.PhotoImage(pil)
                self.camera.config(image=self.photo,text='')
        elif self.engine.error:
            self.camera.config(text='Cannot initialize pipeline.\nSee the error in the right panel.\nUse --preview to test the phone without AI.')
        self.root.after(33,self.tick)

    def close(self):
        if self.closed:
            return
        self.closed=True
        self.engine.close()
        self.audio.close()
        self.root.destroy()


def arguments():
    p=argparse.ArgumentParser(description='DeskGuide Laptop CPU RGB-D workstation')
    p.add_argument('--config',type=Path,default=ROOT/'config.json')
    p.add_argument('--host')
    p.add_argument('--port',type=int)
    p.add_argument('--target',choices=['cup','bottle','bowl'])
    p.add_argument('--rotation',type=int,choices=[0,90,180,270])
    p.add_argument('--object-ms',type=float)
    p.add_argument('--hand-ms',type=float)
    p.add_argument('--preview',action='store_true')
    p.add_argument('--usb',action='store_true',help='Connect through Apple Mobile Device USB service, no Wi-Fi')
    p.add_argument('--usb-check',action='store_true',help='Check Apple USB service and attached iPhone')
    p.add_argument('--no-audio',action='store_true')
    p.add_argument('--audio-test',action='store_true')
    p.add_argument('--check',action='store_true')
    p.add_argument('--benchmark',type=float,metavar='SECONDS',help='Explicit blank-input CPU benchmark, not live validation')
    p.add_argument('--headless',action='store_true')
    p.add_argument('--seconds',type=float,default=30)
    return p.parse_args()


def main():
    args=arguments()
    config=json.loads(args.config.read_text())
    if args.usb:
        config['usb']=True
    for k in ['host','port','target','rotation']:
        if getattr(args,k) is not None:
            config[k]=getattr(args,k)
    if args.object_ms is not None:
        config['object_interval_ms']=max(10,args.object_ms)
    if args.hand_ms is not None:
        config['hand_interval_ms']=max(10,args.hand_ms)
    network_run=not (args.usb_check or args.audio_test or args.check or args.benchmark is not None)
    if network_run and not config.get('usb',False) and not config.get('host'):
        print('Connect iPhone to the existing TONY 0864 hotspot and start PathOn. '
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
    elif args.audio_test:
        self_test(config['audio_device'])
    elif args.benchmark is not None:
        from .benchmark import benchmark
        benchmark(config,args.benchmark)
    elif args.check:
        from .models import ObjectDetector,HandDetector
        import platform
        cv2.setNumThreads(config['opencv_threads'])
        detector=ObjectDetector()
        hand=HandDetector()
        blank=np.zeros((480,640,3),np.uint8)
        print('Python',platform.python_version(),'OpenCV',cv2.__version__)
        print('CPU object detections on blank input:',len(detector.detect(blank,'cup')))
        print('Hand detections on blank input:',len(hand.detect(blank)))
        hand.close()
        print('Model initialization and execution passed. No live hardware validated.')
    elif args.headless:
        audio=Audio(config['audio_volume'],config['audio_device'])
        if not args.no_audio and not args.preview:
            audio.start()
        engine=Engine(config,audio,args.preview)
        engine.start()
        deadline=time.monotonic()+args.seconds
        try:
            while time.monotonic()<deadline and not engine.error:
                time.sleep(.1)
        finally:
            engine.close()
            audio.close()
        print(json.dumps(dict(error=engine.error,phone=engine.phone.status,log=str(engine.log_path),
                              last_metrics=None if engine.result is None else engine.result['metrics']),indent=2))
        if engine.error:
            raise SystemExit(1)
        if engine.result is None:
            print('No live frames received; phone connection is not validated.')
            raise SystemExit(2)
    else:
        window=Window(config,args.preview,args.no_audio)
        window.root.mainloop()
