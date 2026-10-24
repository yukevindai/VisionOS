# Shared frame interface for Claude's isolated hand module

This is the R3 desktop preview boundary, not the native R1/Swift contract. Target integration
owns `r3_vision/frame_packet.py`, `live_tracking.py`, and the single PathOn `Source`. The hand
module must not open another stream, resize/rotate/mirror the shared input, or edit those files.
The target owner supplies `r3_vision/hand_bridge.py`, wrapping Claude's public API without
editing its isolated files. The combined preview runs hand and target inference on one packet
in the local CV worker. This is integrated in code and offline-tested; not a live-device pass.

## Input

Consume `FramePacket` from `TrackingWorker.latest_frame`. Its `image` is a read-only-by-convention
Pillow RGB image, already upright, full field of view, and uniformly reduced to <=960 px on
the long side. Use `np.array(packet.image)` for a private RGB array for MediaPipe. No BGR swap,
extra flip, crop or rotation. Target boxes and hand landmarks both refer to THESE image pixels.
The window may shrink this image further only after both overlays are drawn.

Every hand result must retain `packet.stamp` unmodified. `FrameStamp` records stream ID,
frame ID, source frame ID and reconnect epoch, raw and processed dimensions, applied clockwise
rotation, local receive monotonic seconds, and the original opaque sensor timestamp if available.
No exposure UTC is fabricated. The local receive clock and sensor clock are NOT interchangeable.
For MediaPipe VIDEO/LIVE_STREAM timestamp_ms, use an increasing local sequence derived from
receive time and preserve the original stamp independently; do not alter the stamp to satisfy
a model's timestamp requirement. Internal integer rounding must not be used for joining results.

## Output/bridge expectation

Claude keeps its own module/types. The generic adapter shape is:

```python
{ 'stamp': packet.stamp, 'hands': [
    { 'landmarks': [(x, y), ...], 'handedness': label_or_none,
      'confidence': actual_reported_confidence_or_none }
] }
```

Landmark x/y use the top-left origin in `packet.image`; landmark 8 is the index fingertip.
The actual `HandFrame` reports 21 pixel and normalized x/y points; no metric z is invented.
Source mirroring is unknown (`stamp.mirrored=None`);
do not infer anatomical chirality or guidance-hand identity without validating conventions.
Empty hands or explicit failure means no hand overlay; never reuse the previous landmarks.

Target output is `TrackingWorker.latest_target: TargetOverlay`, containing the identical stamp,
current task generation/state/label, normalized quadrilateral or None, and actually available similarity scores.
These scores are not identity probabilities and cannot authorize physical guidance.

The future preview adapter must use `same_frame(target.stamp, hand.stamp)` for an EXACT join:
stream/frame/epoch, dimensions, transform and timestamps all match. Never combine nearest times,
the last available hand, or different resized/oriented frames. Latest-frame and latest-result
attributes can update separately; stamps must be checked even when polled together. A bounded
buffer may hold matching images/results, but expired (>300ms local receive age), missing,
out-of-order and old-generation results must be hidden/discarded. The overlay generation must
also match `worker.session.generation`. A target change clears published results. Reconnect
clears pending joins.

## Ownership / next step

Claude owns only its isolated MediaPipe hand implementation/tests. The target owner owns the
frame boundary and synchronized overlay bridge. Claude need not change its module
to import this type: returning original frame identity/timestamp with normalized landmarks is
sufficient for an adapter, provided the exact shared image is the input. No sound, haptics,
movement cues or native R1 changes are part of this desktop integration.

Actual bridge call: `HandTracker.process(packet.image, stamp.received_monotonic_s,
frame_id=stamp.frame_id, color_order='RGB')`. It checks the returned frame ID, timestamp and
image size before rendering through Claude's `draw_hands`. Empty/stale/error results do not
reuse previous landmarks. `TrackingWorker.latest_hand` exposes the stamped result. Pass
`--no-hands` for the target-only diagnostic mode. No hand module or hand tests were edited.
