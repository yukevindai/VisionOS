import json
import math


def load_config(args):
    config = json.loads(args.config.read_text(encoding='utf-8'))
    config['target'] = 'can'
    config['detection_mode'] = 'automatic'
    if args.usb:
        config['usb'] = True
    for key in ('host', 'port', 'target', 'rotation'):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    for argument, key in (('object_ms', 'object_interval_ms'), ('hand_ms', 'hand_interval_ms')):
        value = getattr(args, argument)
        if value is not None:
            config[key] = max(10, value)
    limit = float(config['max_frame_age_ms'])
    if not math.isfinite(limit) or not 0 < limit <= 300:
        raise ValueError('Movement guidance freshness must be between 0 and 300 ms.')
    return config
