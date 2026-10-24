import json
import socket
import struct
import threading
import time
import unittest
import cv2
import numpy as np
from deskguide.stream import read_frame,Phone
from deskguide.audio import Audio


def packet(version=2,fid=1):
    rgb=cv2.imencode('.jpg',np.zeros((48,64,3),np.uint8))[1].tobytes()
    depth=np.ones((12,16),dtype='<f4').tobytes()
    header=bytearray(48 if version==2 else 32)
    header[:6]=b'REC3D'+bytes([version])
    struct.pack_into('<IdHHII',header,6,fid,float(fid),16,12,len(depth),len(rgb))
    conf=b''
    if version==2:
        conf=bytes([2])*192
        struct.pack_into('<HHII',header,30,64,48,len(conf),0)
    K=np.array([[60,0,32],[0,60,24],[0,0,1]],dtype='<f4').tobytes()
    return bytes(header)+K+np.eye(4,dtype='<f4').tobytes()+depth+rgb+conf


class Reader:
    def __init__(self,data): self.data=data
    def recv(self,n):
        size=min(n,13)
        part,self.data=self.data[:size],self.data[size:]
        return part


class IOAudio(unittest.TestCase):
    def test_fragmented_wire_v1_v2(self):
        for version in [1,2]:
            f=read_frame(Reader(packet(version)))
            self.assertEqual(f.color.shape,(48,64,3))
            self.assertEqual(f.depth.shape,(12,16))
            self.assertEqual(f.depth[0,0],1.)
            self.assertIsNotNone(f.pose)
            self.assertEqual(f.confidence is None,version==1)

    def test_bad_or_truncated_packet(self):
        with self.assertRaises(ValueError): read_frame(Reader(b'WRONG!'))
        with self.assertRaises(ConnectionError): read_frame(Reader(packet()[:100]))

    def test_oversized_packet_rejected_before_payload(self):
        p=bytearray(packet())
        struct.pack_into('<I',p,26,999999999)
        with self.assertRaises(ValueError): read_frame(Reader(bytes(p)))

    def test_single_latest_slot_and_reconnect(self):
        server=socket.socket()
        server.bind(('127.0.0.1',0))
        server.listen(2)
        server.settimeout(5)
        first_ready=threading.Event()
        release=threading.Event()
        second_ready=threading.Event()
        errors=[]
        def serve():
            try:
                with server.accept()[0] as conn:
                    conn.sendall(b''.join(packet(fid=i) for i in range(1,21)))
                    first_ready.set()
                    release.wait(4)
                with server.accept()[0] as conn:
                    conn.sendall(packet(fid=1))
                    second_ready.set()
                    release.clear()
                    release.wait(4)
            except Exception as exc: errors.append(str(exc))
        worker=threading.Thread(target=serve,daemon=True)
        worker.start()
        phone=Phone('127.0.0.1',server.getsockname()[1])
        phone.start()
        try:
            self.assertTrue(first_ready.wait(3))
            deadline=time.monotonic()+3
            while time.monotonic()<deadline and (phone.latest is None or phone.latest.id!=20):
                time.sleep(.01)
            self.assertEqual(phone.latest.id,20)
            epoch=phone.latest.epoch
            self.assertFalse(hasattr(phone,'queue'))
            release.set()
            self.assertTrue(second_ready.wait(4))
            deadline=time.monotonic()+2
            while time.monotonic()<deadline and (phone.latest is None or phone.latest.epoch==epoch):
                time.sleep(.01)
            self.assertGreater(phone.latest.epoch,epoch)
        finally:
            release.set()
            phone.close()
            server.close()
            worker.join(2)
        self.assertFalse(errors)

    def test_stereo_left_and_right(self):
        for x,channel in [(-.1,0),(.1,1)]:
            audio=Audio()
            audio.update([x,0,0],.3)
            out=np.zeros((4800,2),np.float32)
            audio.callback(out,len(out),None,False)
            self.assertGreater(np.abs(out[:,channel]).sum(),0)
            self.assertEqual(np.abs(out[:,1-channel]).sum(),0)

    def test_stale_and_muted_audio(self):
        audio=Audio()
        audio.update([.1,0,0],.3,deadline=time.monotonic()-1)
        out=np.zeros((4800,2),np.float32)
        audio.callback(out,len(out),None,False)
        self.assertEqual(np.abs(out).sum(),0)
        audio.update([.1,0,0],.3)
        audio.muted=True
        audio.callback(out,len(out),None,False)
        self.assertEqual(np.abs(out).sum(),0)

    def test_audio_continuity_without_restart(self):
        audio=Audio()
        out=np.zeros((480,2),np.float32)
        for _ in range(30):
            audio.update([.1,-.1,.1],.3)
            audio.callback(out,len(out),None,False)
            self.assertTrue(np.isfinite(out).all())
            self.assertLessEqual(np.max(abs(out)),audio.volume+.001)
        self.assertEqual(audio.sample,14400)


if __name__=='__main__': unittest.main()
