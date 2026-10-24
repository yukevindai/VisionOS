"""Adapter only: Claude owns hand_tracking/, this module owns exact-frame preview joining."""
from dataclasses import dataclass

from .frame_packet import FrameStamp, same_frame


@dataclass(frozen=True)
class HandOverlay:
    stamp: FrameStamp
    result: object  # hand_tracking.HandFrame, imported lazily with its optional dependencies.


class HandBridge:
    def __init__(self, tracker=None):
        if tracker is None:
            from hand_tracking import HandTracker
            tracker = HandTracker()
        self.tracker = tracker
        self.geometry = None

    def process(self, packet):
        stamp = packet.stamp
        geometry = (stamp.stream_id, stamp.connection_epoch, stamp.image_width,
                    stamp.image_height, stamp.rotation_clockwise, stamp.mirrored)
        if geometry != self.geometry:
            self.tracker.reset()
            self.geometry = geometry
        # Claude's public API takes RGB and local monotonic seconds. Its "capture" field is
        # explicitly the desktop receive clock here, never a fabricated sensor exposure UTC.
        result = self.tracker.process(packet.image, stamp.received_monotonic_s,
                                      frame_id=stamp.frame_id, color_order='RGB')
        if (result.frame_id != stamp.frame_id or result.capture_timestamp_s != stamp.received_monotonic_s
                or result.image_size != (stamp.image_width, stamp.image_height)):
            raise ValueError('Hand result frame/timestamp/geometry mismatch')
        return HandOverlay(stamp, result)

    @staticmethod
    def draw(image, packet, overlay, now):
        if (overlay is None or not same_frame(packet.stamp, overlay.stamp) or
                not 0 <= now-packet.stamp.received_monotonic_s <= .3 or image.size != packet.image.size):
            return image.copy()
        from hand_tracking.render import draw_hands
        return draw_hands(image, overlay.result, frame_id=packet.stamp.frame_id)

    def close(self):
        self.tracker.close()
