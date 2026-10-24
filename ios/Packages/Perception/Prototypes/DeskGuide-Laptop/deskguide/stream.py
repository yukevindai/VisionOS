"""Independent REC3D v1/v2 wire reader. No SDK code is bundled."""
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
import cv2
import numpy as np


@dataclass
class Frame:
    id: int
    timestamp: float
    received: float
    color: np.ndarray
    depth: np.ndarray
    K: np.ndarray
    pose: np.ndarray | None
    confidence: np.ndarray | None
    epoch: int = 0
    relative_lag: float = 0.
    decode_ms: float = 0.


def exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        part = sock.recv(count - len(chunks))
        if not part:
            raise ConnectionError('Phone disconnected')
        chunks.extend(part)
    return bytes(chunks)


def read_packet(sock):
    """Drain one complete paired packet without decoding its JPEG."""
    prefix = exact(sock, 6)
    if prefix[:5] != b'REC3D' or prefix[5] not in (1, 2):
        raise ValueError('Unsupported PathOn protocol; expected REC3D v1/v2')
    version = prefix[5]
    h = prefix + exact(sock, (48 if version == 2 else 32) - 6)
    fid, ts, w, ht, ds, rs = struct.unpack_from('<IdHHII', h, 6)
    cs, ims = struct.unpack_from('<II', h, 34) if version == 2 else (0, 0)
    if not (0 < w <= 2048 and 0 < ht <= 2048 and ds == w * ht * 4
            and 0 < rs <= 16000000 and cs in (0, w * ht) and ims <= 4096
            and np.isfinite(ts)):
        raise ValueError('Invalid frame dimensions or payload sizes')
    K = np.frombuffer(exact(sock, 36), '<f4').reshape(3, 3).copy()
    pose = np.frombuffer(exact(sock, 64), '<f4').reshape(4, 4).copy()
    depth = np.frombuffer(exact(sock, ds), '<f4').reshape(ht, w).copy()
    jpeg = exact(sock, rs)
    conf = np.frombuffer(exact(sock, cs), np.uint8).reshape(ht, w).copy() if cs else None
    exact(sock, ims)
    received=time.monotonic()  # last wire byte, BEFORE decoding/processing
    return fid,ts,K,pose,depth,jpeg,conf,version,h,received


def decode_packet(packet):
    fid,ts,K,pose,depth,jpeg,conf,version,h,received=packet
    started=time.monotonic()
    color=cv2.imdecode(np.frombuffer(jpeg,np.uint8),cv2.IMREAD_COLOR)
    if (color is None or not np.isfinite(K).all() or min(K[0, 0], K[1, 1]) <= 0
            or not np.allclose(K[2],[0,0,1],atol=.001) or abs(np.linalg.det(K)) < .001):
        raise ValueError('Invalid JPEG/intrinsics')
    if version == 2:
        rw, rh = struct.unpack_from('<HH', h, 30)
        if (rw, rh) != (color.shape[1], color.shape[0]):
            raise ValueError('RGB dimensions disagree')
    # Wire K is at RGB resolution. Keep images unrotated for all geometry.
    valid = (np.isfinite(pose).all() and np.allclose(pose[3], [0, 0, 0, 1], atol=.001)
             and np.allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=.02)
             and np.linalg.det(pose[:3, :3]) > .98)
    return Frame(fid,ts,received,color,depth,K,pose if valid else None,conf,
                 decode_ms=(time.monotonic()-started)*1000)


def read_frame(sock):
    """Synchronous reader retained for files and protocol smoke checks."""
    return decode_packet(read_packet(sock))


class Phone:
    def __init__(self, host, port, usb=False):
        self.host, self.port = host, port
        self.usb = usb
        self.latest = None
        self.status = 'Connecting'
        self.fps = 0.
        self.sensor_fps = 0.
        self.stop_event = threading.Event()
        self.sock = None
        self.packet=None
        self.packet_ready=threading.Event()
        self.connection_epoch=0
        self.decode_thread=threading.Thread(target=self.decode,name='phone-decode',daemon=True)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.decode_thread.start()
        self.thread.start()

    def decode(self):
        previous=None
        while not self.stop_event.is_set():
            self.packet_ready.wait(.05)
            self.packet_ready.clear()
            item=self.packet
            if item is None or item is previous:
                continue
            previous=item
            packet,epoch,lag=item
            try:
                f=decode_packet(packet)
            except (ValueError,cv2.error) as exc:
                self.status=str(exc)
                self.latest=None
                continue
            # An in-flight decode from a disconnected phone cannot reappear.
            if epoch==self.connection_epoch and not self.stop_event.is_set():
                f.epoch=epoch
                f.relative_lag=lag
                self.latest=f

    def run(self):
        epoch = 0
        while not self.stop_event.is_set():
            try:
                if self.usb:
                    from .usb import connect
                    connection = connect(self.port)
                else:
                    connection = socket.create_connection((self.host, self.port), timeout=3)
                with connection as sock:
                    self.sock = sock
                    sock.settimeout(3)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    epoch += 1
                    self.connection_epoch=epoch
                    previous = None
                    last_ts = None
                    clock_floor=float('inf')
                    arrivals=deque()
                    sensor_times=deque(maxlen=60)
                    while not self.stop_event.is_set():
                        packet=read_packet(sock)
                        timestamp,received=packet[1],packet[-1]
                        if last_ts is not None and timestamp <= last_ts:
                            raise ValueError('Nonmonotonic sensor timestamp')
                        last_ts=timestamp
                        offset=received-timestamp
                        clock_floor=min(clock_floor,offset)
                        # Retain the conservative floor for this connection;
                        # sustained network delay must not age out of the gate.
                        lag=max(0.,offset-clock_floor)
                        arrivals.append(received)
                        while len(arrivals)>2 and arrivals[-1]-arrivals[0]>1.:
                            arrivals.popleft()
                        if len(arrivals)>1 and arrivals[-1]-arrivals[0]>.05:
                            self.fps=(len(arrivals)-1)/(arrivals[-1]-arrivals[0])
                        sensor_times.append(timestamp)
                        if len(sensor_times)>1:
                            self.sensor_fps=(len(sensor_times)-1)/(sensor_times[-1]-sensor_times[0])
                        # Overwrite compressed packets too: CPU decoding never
                        # prevents the socket reader draining the TCP stream.
                        self.packet=(packet,epoch,lag)
                        self.packet_ready.set()
                        self.status = ('USB' if self.usb else 'Wi-Fi')+' connected / paired RGB-D packet'
            except (OSError, ValueError, ConnectionError) as exc:
                if self.stop_event.is_set():
                    self.status='Stopped'
                    break
                self.status = str(exc)
                self.connection_epoch=-1
                self.packet=None
                self.latest = None
                self.stop_event.wait(1)

    def close(self):
        self.stop_event.set()
        self.packet_ready.set()
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.thread.join(timeout=4)
        self.decode_thread.join(timeout=4)


def prepare(frame, max_width=960, rotation=0):
    """Rotate pixels, depth, intrinsics AND optical camera basis consistently."""
    from dataclasses import replace
    color,confidence=frame.color,frame.confidence
    depth=np.array(frame.depth,dtype=np.float32,copy=True)
    depth[~np.isfinite(depth)]=0.
    if confidence is not None:
        depth[confidence<1]=0.
    K,pose=frame.K.copy(),None if frame.pose is None else frame.pose.copy()
    # Match the verified input contract: resize native RGB/K first, align depth
    # with nearest-neighbor sampling, then rotate the registered pixel grids.
    h,w=color.shape[:2]
    scale=min(1.,max_width/max(w,h))
    if scale<1:
        nw,nh=round(w*scale),round(h*scale)
        color=cv2.resize(color,(nw,nh),interpolation=cv2.INTER_AREA)
        K=np.diag([nw/w,nh/h,1.])@K
    target=(color.shape[1],color.shape[0])
    depth=cv2.resize(depth,target,interpolation=cv2.INTER_NEAREST)
    if confidence is not None:
        confidence=cv2.resize(confidence,target,interpolation=cv2.INTER_NEAREST)
    for _ in range((rotation//90)%4):
        h,w=color.shape[:2]
        A=np.array([[0,-1,h-1],[1,0,0],[0,0,1.]])
        R=np.array([[0,-1,0],[1,0,0],[0,0,1.]])
        K=A@K@R.T
        if pose is not None:
            axes=np.diag([1.,-1.,-1.,1.])
            r4=np.eye(4)
            r4[:3,:3]=R
            pose=pose@axes@r4.T@axes
        color=cv2.rotate(color,cv2.ROTATE_90_CLOCKWISE)
        depth=cv2.rotate(depth,cv2.ROTATE_90_CLOCKWISE)
        if confidence is not None:
            confidence=cv2.rotate(confidence,cv2.ROTATE_90_CLOCKWISE)
    return replace(frame,color=color,depth=depth,confidence=confidence,K=K,pose=pose)
