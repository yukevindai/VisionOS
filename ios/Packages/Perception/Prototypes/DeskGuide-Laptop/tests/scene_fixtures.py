import numpy as np
from deskguide.stream import Frame


def scene(shift=0, depth=0.55):
    rng = np.random.default_rng(92)
    color = np.full((240, 320, 3), 25, np.uint8)
    texture = rng.integers(40, 240, (100, 50, 3), np.uint8)
    color[65:165, 110 + shift:160 + shift] = texture
    return Frame(1, 1.0, 1.0, color, np.full((240, 320), depth, np.float32), np.array([[300.0, 0, 160], [0, 300, 120], [0, 0, 1]]), np.eye(4), np.ones((240, 320), np.uint8) * 2, 1)
