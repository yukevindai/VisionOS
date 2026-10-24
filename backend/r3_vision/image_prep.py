"""Orientation-correct an uploaded photo once, so every box refers to the same upright image.

The shared convention is normalized coordinates, origin top-left, after orientation correction. EXIF
rotation is applied before Gemini sees the image; normalized boxes are therefore valid for the upright
image at its original resolution, whatever size was sent to the provider.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps

MAX_SENT_EDGE_PX = 1536
EXIF_ORIENTATION_TAG = 0x0112


@dataclass(frozen=True)
class PreparedImage:
    jpeg: bytes
    width_px: int  # upright, original resolution
    height_px: int
    sent_width_px: int
    sent_height_px: int
    exif_orientation: int  # 1 = already upright

    def info(self) -> dict:
        return {"widthPx": self.width_px, "heightPx": self.height_px,
                "exifOrientation": self.exif_orientation, "orientationApplied": True}


def prepare(data: bytes, max_edge: int = MAX_SENT_EDGE_PX) -> PreparedImage:
    """Raises ValueError when the bytes are not a readable image."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            orientation = int(source.getexif().get(EXIF_ORIENTATION_TAG, 1) or 1)
            upright = ImageOps.exif_transpose(source).convert("RGB")
    except Exception as error:  # Pillow raises several unrelated types for bad input
        raise ValueError(f"unreadable image: {error}") from error
    width, height = upright.size
    sent = upright.copy()
    sent.thumbnail((max_edge, max_edge))  # keeps aspect ratio, so normalized boxes are unchanged
    buffer = io.BytesIO()
    sent.save(buffer, format="JPEG", quality=90)
    return PreparedImage(buffer.getvalue(), width, height, sent.size[0], sent.size[1], orientation)
