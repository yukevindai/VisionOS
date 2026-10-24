"""One persistent PortAudio callback with expiring metric-only commands."""
import time
import numpy as np


class Audio:
    def __init__(self,volume=.12,device=None):
        self.volume=volume
        self.device=device
        self.muted=False
        self.command=None
        self.phase=0.
        self.beat=0.
        self.sample=0
        self.gain=0.
        self.complete_until=0.
        self.underruns=0
        self.stream=None
        self.status='OFF'

    def start(self):
        import sounddevice as sd
        self.stream=sd.OutputStream(samplerate=48000,channels=2,blocksize=480,
                                    dtype='float32',device=self.device,callback=self.callback)
        self.stream.start()
        self.status='READY'

    def update(self,delta=None,distance=None,deadline=None):
        if delta is None or distance is None:
            self.command=None
        else:
            self.command=(min(time.monotonic()+.18,deadline) if deadline is not None else time.monotonic()+.18,
                          np.array(delta,dtype=float),float(distance))

    def callback(self,out,frames,timing,status):
        if status:
            self.underruns+=1
        now=time.monotonic()
        command=self.command
        valid=command is not None and now<command[0] and not self.muted
        complete=now<self.complete_until and not self.muted
        target_gain=self.volume if valid or complete else 0.
        ramp=np.linspace(self.gain,target_gain,frames)
        self.gain=target_gain
        # Cycle axis slots: horizontal, vertical, depth. Pitch/chirp identifies each slot.
        slot=int(self.sample/48000/.3)%3
        self.sample+=frames
        pan=0.
        pitch=600.
        rate=2.
        chirp=0.
        if valid:
            _,delta,distance=command
            rate=1.5+6*np.clip(1-distance/.8,0,1)
            if slot==0:
                pan=float(np.clip(delta[0]/.04,-1,1))
                pitch=650.
            elif slot==1:
                pitch=950. if delta[1]<0 else 400.
                chirp=400. if delta[1]<0 else -180.
            else:
                pitch=1250. if delta[2]>0 else 250.
            if abs(delta[slot])<.025:
                ramp*=.25
        if complete:
            pitch,rate=1046.,5.
        t=np.arange(frames)/48000
        beats=self.beat+t*rate
        pulse=beats%1
        envelope=np.clip(pulse/.025,0,1)*np.clip((.19-pulse)/.025,0,1)
        self.beat=float((beats[-1]+rate/48000)%1)
        phase=self.phase+2*np.pi*(pitch*t+.5*chirp*t*t)
        self.phase=float((phase[-1]+2*np.pi*pitch/48000)%(2*np.pi))
        wave=np.sin(phase)*envelope*ramp
        out[:,0]=wave*np.sqrt((1-pan)/2)
        out[:,1]=wave*np.sqrt((1+pan)/2)
        self.status='MUTED' if self.muted else ('GUIDING' if valid else 'PAUSED')

    def close(self):
        self.command=None
        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.status='OFF'


def self_test(device=None):
    audio=Audio(device=device)
    audio.start()
    try:
        for axis,name,direction in [(0,'LEFT',-1),(0,'RIGHT',1),(1,'UP',-1),
                                     (1,'DOWN',1),(2,'FORWARD',1),(2,'BACK',-1)]:
            print(name,flush=True)
            for _ in range(15):
                audio.sample=int(axis*.3*48000)
                delta=np.zeros(3)
                delta[axis]=direction*.1
                audio.update(delta,.3)
                time.sleep(.1)
        audio.update()
        audio.complete_until=time.monotonic()+.7
        time.sleep(.8)
        print('Completed device test. Confirm headphone routing by listening.')
    finally:
        audio.close()
