"""R3-local shared 2D frame interface for the future hand/target preview adapter.

One capture owner; consumers must retain the exact stamp on every result. No 3D semantics.
"""
from dataclasses import dataclass
import math

from PIL import Image

from .live_camera import upright
from .visual_tracker import working_image


@dataclass(frozen=True)
class FrameStamp:
    stream_id: str
    frame_id: str
    connection_epoch: int | None
    source_frame_id: str | None
    received_monotonic_s: float
    sensor_timestamp: float | None
    image_width: int
    image_height: int
    raw_width: int
    raw_height: int
    rotation_clockwise: int
    mirrored: bool | None = None  # Source mirroring is unverified; no extra mirror is applied.


@dataclass(frozen=True)
class FramePacket:
    stamp: FrameStamp
    image: Image.Image  # RGB, upright, full field of view, <=960px long side; treat as read-only.


@dataclass(frozen=True)
class TargetOverlay:
    stamp: FrameStamp
    generation: int
    state: str
    label: str
    polygon_normalized: tuple[tuple[float, float], ...] | None
    tracker_score: float | None
    appearance_correlation: float | None


def make_packet(raw, received, info, stream_id, rotation, max_edge=960, upright_image=None):
    if rotation not in (0, 90, 180, 270) or not math.isfinite(received) or not 0 < max_edge <= 960:
        raise ValueError('Invalid frame orientation/receive timestamp/size')
    image = working_image(upright_image if upright_image is not None else upright(raw, rotation), max_edge)
    source_id = str(info['sourceFrameID']) if info.get('sourceFrameID') is not None else None
    epoch = info.get('connectionEpoch')
    frame_id = f'{stream_id}:{epoch}:{source_id if source_id is not None else float(received).hex()}'
    stamp = FrameStamp(stream_id, frame_id, epoch, source_id, received, info.get('sensorTimestamp'),
                       image.width, image.height, raw.width, raw.height, rotation)
    return FramePacket(stamp, image)


def same_frame(a: FrameStamp, b: FrameStamp) -> bool:
    """Do not pair nearest timestamps or different epochs/orientations/resolutions."""
    return a == b


def raw_normalized_to_image(x, y, rotation):
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
        raise ValueError('Point outside normalized raw image')
    return {0: (x, y), 90: (1-y, x), 180: (1-x, 1-y), 270: (y, 1-x)}[rotation]
