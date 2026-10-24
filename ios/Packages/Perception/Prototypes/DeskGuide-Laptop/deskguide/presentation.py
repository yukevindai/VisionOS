from dataclasses import dataclass
import math
from .geometry import instructions


@dataclass(frozen=True)
class DisplayState:
    stale: bool
    active: bool
    estimated: bool
    distance: str
    directions: str
    status: str
    hand: str
    depth: str
    target: str
    message: str


def display_state(result, now, max_age_ms=300, completed=False, error=''):
    frame, metrics = result['frame'], result['metrics']
    age = now - frame.received + frame.relative_lag
    stale = not math.isfinite(age) or not 0 <= age <= min(300, max_age_ms) / 1000
    active = bool(metrics['metric_valid'] and not stale and not completed and not error)
    distance = metrics.get('display_distance_m') if not stale and not completed and not error else None
    delta = metrics.get('display_delta_m')
    estimated = metrics.get('distance_kind') == 'ESTIMATED' and distance is not None
    hand = 'LOST' if stale or error or result['finger_uv'] is None else 'ACTIVE'
    source = metrics.get('hand_depth_source', '')
    depth = ('UNAVAILABLE' if stale or error else 'LIDAR' if source.startswith('palm LiDAR')
             else 'ESTIMATED' if metrics.get('finger_estimated') else 'UNAVAILABLE')
    target = metrics['object_state'].replace('_', ' ')
    status = 'GUIDING' if active else 'PAUSED'
    if distance is not None and delta is not None:
        words = instructions(delta)
        directions = '\n'.join(
            f'{axis:<8}  ' + ('ALIGNED' if word == 'ALIGNED' else f"{word} {'≈ ' if estimated else ''}{abs(value) * 100:.0f} cm")
            for axis, word, value in zip(('SIDE', 'HEIGHT', 'REACH'), words, delta))
    else:
        directions = 'SIDE       —\nHEIGHT   —\nREACH    —'
    if error:
        message = error
    elif stale:
        message = 'Waiting for a fresh camera frame. Guidance stopped.'
    elif completed:
        message = 'Guidance paused. Select Find again to restart.'
    elif estimated:
        message = 'Estimated distance · hand size/shape. Guidance stopped.'
    elif active:
        message = 'Move slowly. Guidance uses current supported depth.'
    elif metrics.get('image_dark'):
        message = 'Camera view is dark. Check the rear camera.'
    elif hand == 'LOST':
        message = 'Keep your palm and index fingertip inside the camera view.'
    elif target == 'SEARCHING':
        message = 'Looking for the beverage can.'
    else:
        message = metrics.get('distance_reason') or 'Waiting for reliable hand and target depth.'
    return DisplayState(stale, active, estimated,
                        f"{'≈ ' if estimated else ''}{distance * 100:.1f} cm" if distance is not None else 'Unavailable',
                        directions, status, hand, depth, target, message)
