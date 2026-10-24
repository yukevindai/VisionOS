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


def draw_hand_observation(image,landmarks,fingertip,stale=False):
    """Draw the observation on its OWN frame, independently of depth validity."""
    if stale:
        return  # Never show old fingertip/landmarks as a live observation.
    color=(40,170,240) if stale else (60,220,90)
    if landmarks is not None:
        points=np.asarray(landmarks,float)
        if points.shape==(21,2) and np.isfinite(points).all():
            points=np.rint(points).astype(int)
            for chain in ((0,1,2,3,4),(0,5,6,7,8),(5,9,10,11,12),
                          (9,13,14,15,16),(13,17,18,19,20),(0,17)):
                for a,b in zip(chain,chain[1:]):
                    cv2.line(image,tuple(points[a]),tuple(points[b]),color,1,cv2.LINE_AA)
            for p in points:
                cv2.circle(image,tuple(p),2,color,-1,cv2.LINE_AA)
    if fingertip is not None and np.isfinite(fingertip).all():
        cv2.circle(image,tuple(np.rint(fingertip).astype(int)),8,(255,240,90),2,cv2.LINE_AA)


class Window:
    def __init__(self,config,preview=False,no_audio=False):
        self.config=config
        self.root=tk.Tk()
        self.root.title('DeskGuide | Beverage can guidance')
        window_width=min(1400,self.root.winfo_screenwidth()-80)
        window_height=min(900,self.root.winfo_screenheight()-100)
        self.root.geometry(f'{window_width}x{window_height}')
        self.root.minsize(min(1000,window_width),min(700,window_height))
        self.root.configure(bg='#181a1b')
        self.root.columnconfigure(0,weight=7)
        self.root.columnconfigure(1,weight=3,minsize=350)
        self.root.rowconfigure(0,weight=1)
        self.camera=tk.Label(self.root,bg='#080a0b',fg='#b6c6df',text='Connecting to iPhone…',font=('Segoe UI',18))
        self.camera.grid(row=0,column=0,sticky='nsew')
        panel=tk.Frame(self.root,bg='#181a1b',padx=22,pady=16)
        panel.grid(row=0,column=1,sticky='nsew')
        self.font_scale=min(1.,window_height/850)
        def label(text,size,color='#f4f4f4'):
            item=tk.Label(panel,text=text,font=('Segoe UI',max(8,round(size*self.font_scale))),bg='#181a1b',fg=color,justify='left',anchor='w',wraplength=300)
            item.pack(fill='x',pady=2)
            return item
        label('LOOKING FOR',12,'#9b9d9f')
        self.target_label=label('beverage can',30)
        self.target_label.config(font=('Segoe UI',round(28*self.font_scale),'bold'))
        self.status_chip=label('SEARCHING',18,'#99a0a6')
        label('DISTANCE',11,'#9b9d9f')
        self.distance=label('— cm',58)
        self.distance.config(font=('Segoe UI',round(52*self.font_scale),'bold'))
        label('MOVE HAND',11,'#9b9d9f')
        self.directions=label('SIDE       —\nHEIGHT   —\nREACH    —',16)
        self.states=label('',11)
        self.performance=label('',9,'#9eb1cb')
        self.message=label('',9,'#e3b579')
        self.selection_help=label('Automatic target detection\nR searches again.',9,'#9b9d9f')
        controls=tk.Frame(panel,bg='#181a1b')
        controls.pack(fill='x',pady=4)
        controls.columnconfigure(0,weight=1)
        controls.columnconfigure(1,weight=1)
        for i,(text,action) in enumerate([('Find again [R]',self.reset),('Mute [M]',self.mute),
                            ('Rotate [O]',self.rotate),('Fullscreen [F11]',self.full)]):
            ttk.Button(controls,text=text,command=action).grid(row=i//2,column=i%2,sticky='ew',padx=2,pady=3)
        label('Distances are approximate',9,'#8299b6')
        self.depth_preview=tk.Label(panel,bg='#080a0b')
        self.depth_preview.pack(anchor='e',pady=4)
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
        self.preview_mapping=None
        for key,action in [('r',self.reset),('m',self.mute),('d',self.depth),('v',self.mark),
                           ('o',self.rotate),
                           ('<F11>',self.full),('<Return>',self.complete),('<Escape>',self.escape)]:
            self.root.bind(key,lambda event,fn=action:fn())
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.engine.start()
        self.tick()

    def reset(self):
        self.engine.reset()

    @staticmethod
    def image_point(event,mapping):
        frame,x,y,width,height=mapping
        if not (x<=event.x<x+width and y<=event.y<y+height):
            return None
        return np.array([(event.x-x)*frame.color.shape[1]/width,
                         (event.y-y)*frame.color.shape[0]/height])

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
            display_distance=m.get('display_distance_m') if not stale and not self.engine.completed else None
            display_delta=m.get('display_delta_m')
            estimated=m.get('distance_kind')=='ESTIMATED' and display_distance is not None
            self.target_label.config(text='beverage can')
            state='GUIDING' if valid else ('STALE / PAUSED' if stale else 'ESTIMATED / PAUSED' if estimated else m['object_state'].replace('_',' '))
            self.status_chip.config(text=state,fg='#4cde49' if valid else '#e3b579')
            self.distance.config(text=f"≈ {display_distance*100:.1f} cm" if display_distance is not None else 'Unavailable')
            if display_distance is not None and display_delta is not None:
                words=instructions(display_delta)
                text='\n'.join(f'{axis:<8}  '+('ok' if word=='ALIGNED' else f'{word} {abs(value)*100:.0f} cm')
                               for axis,word,value in zip(('SIDE','HEIGHT','REACH'),words,display_delta))
            elif self.engine.completed:
                text='Completion confirmed'
            else:
                text='SIDE       —\nHEIGHT   —\nREACH    —'
            self.directions.config(text=text)
            hand_status='LOST' if stale or result['finger_uv'] is None else 'ACTIVE'
            depth_status='UNAVAILABLE' if stale else 'LIDAR' if m.get('hand_depth_source','').startswith('palm LiDAR') else 'ESTIMATED' if m.get('finger_estimated') else 'UNAVAILABLE'
            self.states.config(text=f"Hand tracking: {hand_status}\nDepth: {depth_status}\nGuidance: {'ACTIVE' if valid else 'PAUSED'}\nTarget: {m['object_state'].replace('_',' ')}")
            self.performance.config(text=f"Loop {m['processing_fps']:.1f} fps · received {m['receive_fps']:.1f}\nFrame age {age*1000:.0f} ms · processing {m['processing_ms']:.0f} ms\nCPU {m['cpu_percent']:.0f}% · RAM {m['rss_mb']:.0f} MB")
            if not error:
                if stale:
                    self.message.config(text='Camera frame is stale; guidance paused.')
                elif m.get('image_dark'):
                    self.message.config(text='Camera image is dark. Uncover rear camera and check PathOn preview.')
                else:
                    reason=m.get('distance_reason','')
                    text=('Hand depth is estimated; guidance paused.' if estimated else
                          '' if valid else 'Looking for the beverage can.' if m['object_state']=='SEARCHING' else
                          'Show your index fingertip.' if hand_status=='LOST' else
                          'Target depth unavailable.' if reason.startswith('Target:') else
                          'Waiting for reliable hand depth.')
                    self.message.config(text=text)
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
                        cv2.putText(image,'Target',(a,max(18,b-7)),0,.5,color,1,cv2.LINE_AA)
                    if valid and result['surface_uv'] is not None and result['finger_uv'] is not None:
                        a=tuple(np.rint(result['finger_uv']).astype(int))
                        b=tuple(np.rint(result['surface_uv']).astype(int))
                        cv2.line(image,a,b,(0,200,255),3,cv2.LINE_AA)
                        cv2.putText(image,f"~{m['distance_m']*100:.0f} cm",((a[0]+b[0])//2,(a[1]+b[1])//2),0,.7,(0,200,255),2,cv2.LINE_AA)
                draw_hand_observation(image,result.get('hand_landmarks'),result['finger_uv'],stale)
                if stale:
                    cv2.rectangle(image,(0,0),(image.shape[1],52),(40,40,220),-1)
                    cv2.putText(image,'STALE - GUIDANCE PAUSED',(15,35),0,.7,(255,255,255),2)
                if self.show_depth:
                    dep=cv2.applyColorMap(np.uint8(np.clip(np.nan_to_num(f.depth)/3,0,1)*255),cv2.COLORMAP_TURBO)
                    thumb=cv2.resize(dep,(128,96))
                    if image.shape[0]>=96 and image.shape[1]>=128:
                        image[:96,:128]=thumb
                pil=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
                pil.thumbnail((width,height))
                displayed=f
                self.preview_mapping=(displayed,(width-pil.width)/2,(height-pil.height)/2,pil.width,pil.height)
                self.photo=ImageTk.PhotoImage(pil)
                self.camera.config(image=self.photo,text='')
                depth=cv2.applyColorMap(np.uint8(np.clip(np.nan_to_num(f.depth)/3,0,1)*255),cv2.COLORMAP_TURBO)
                depth[f.depth<=0]=0
                depth_pil=Image.fromarray(cv2.cvtColor(depth,cv2.COLOR_BGR2RGB))
                depth_pil.thumbnail((160,max(50,round(80*self.font_scale))))
                self.depth_photo=ImageTk.PhotoImage(depth_pil)
                self.depth_preview.config(image=self.depth_photo)
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
    p.add_argument('--target',choices=['can'])
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
    config['target']='can'  # User-facing name; verified model prediction is COCO cup/41.
    config['detection_mode']='automatic'
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
        print('Benchmark mode is disabled in this focused build.')
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
