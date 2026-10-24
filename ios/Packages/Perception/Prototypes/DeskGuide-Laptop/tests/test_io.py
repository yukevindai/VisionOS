import json
import socket
import struct
import threading
import time
import unittest
import cv2
import numpy as np
from deskguide.stream import read_frame,Phone
from deskguide.guidance import GuidanceOutput


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


class WireIO(unittest.TestCase):
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


class GuidanceEvents(unittest.TestCase):
    def test_dominant_axis_directions(self):
        for delta,name in [((-.1,0,0),'LEFT'),((.1,0,0),'RIGHT'),((0,-.1,0),'UP'),
                           ((0,.1,0),'DOWN'),((0,0,.1),'FORWARD'),((0,0,-.1),'BACKWARD')]:
            guidance=GuidanceOutput()
            guidance.update(delta,.1)
            self.assertEqual(guidance.current().direction,name)
            self.assertEqual(guidance.current().reason,'measured')

    def test_stop_when_unavailable_expired_or_within_tolerance(self):
        guidance=GuidanceOutput()
        self.assertEqual(guidance.status,'STOP')
        guidance.update([.1,0,0],.3,deadline=time.monotonic()-1)
        self.assertEqual(guidance.current().reason,'expired')
        guidance.update([.01,-.01,.02],.03)
        self.assertEqual((guidance.status,guidance.current().reason),('STOP','within_tolerance'))
        guidance.update([.1,0,0],.1)
        guidance.update()
        self.assertEqual((guidance.status,guidance.current().reason),('STOP','unavailable'))

    def test_axis_hysteresis_holds_until_clearly_dominant(self):
        guidance=GuidanceOutput()
        guidance.update([.10,0,0],.1)
        guidance.update([.10,0,.11],.15)
        self.assertEqual(guidance.status,'RIGHT')
        guidance.update([.10,0,.13],.16)
        self.assertEqual(guidance.status,'FORWARD')


if __name__=='__main__': unittest.main()
