from typing import Protocol
from .stream import Frame


class FrameSource(Protocol):
    latest: Frame | None
    status: str
    fps: float
    sensor_fps: float

    def start(self) -> None: ...
    def close(self) -> None: ...


class GuidanceSink(Protocol):
    status: str

    def update(self,delta=None,distance=None,deadline=None) -> None: ...
