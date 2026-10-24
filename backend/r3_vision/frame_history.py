"""Bounded JPEG history for catching a delayed identification up to live video."""
from collections import deque
from dataclasses import dataclass
import io
import threading

from PIL import Image

from .visual_tracker import working_image


@dataclass(frozen=True)
class BufferedFrame:
    received: float
    epoch: object
    jpeg: bytes

    def image(self):
        with Image.open(io.BytesIO(self.jpeg)) as im:
            return im.convert('RGB')


class FrameHistory:
    def __init__(self, seconds=75., max_bytes=48*1024*1024, max_frames=800):
        self.seconds, self.max_bytes, self.max_frames = seconds, max_bytes, max_frames
        self.frames = deque()
        self.bytes = 0
        self.lock = threading.Lock()

    def append(self, image, received, epoch):
        stream = io.BytesIO()
        working_image(image).save(stream, 'JPEG', quality=80)
        frame = BufferedFrame(received, epoch, stream.getvalue())
        with self.lock:
            self.frames.append(frame)
            self.bytes += len(frame.jpeg)
            while (self.frames and (received-self.frames[0].received > self.seconds or
                   self.bytes > self.max_bytes or len(self.frames) > self.max_frames)):
                self.bytes -= len(self.frames.popleft().jpeg)

    def after(self, received, epoch):
        with self.lock:
            return [f for f in self.frames if f.received > received and f.epoch == epoch]
