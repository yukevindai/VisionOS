"""2D relation between the index fingertip and the tracked target box in one image. Not touch detection."""
import math

from PIL import ImageDraw


def fingertip_alignment(polygon, tip, size):
    xs, ys = [float(p[0]) for p in polygon], [float(p[1]) for p in polygon]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    tx, ty = float(tip[0]), float(tip[1])
    gap = math.hypot(max(x0 - tx, 0, tx - x1), max(y0 - ty, 0, ty - y1))
    return dict(dx_px=cx - tx, dy_px=cy - ty, center_distance_px=math.hypot(cx - tx, cy - ty),
                edge_gap_px=gap, inside_box=gap == 0, center=(cx, cy),
                center_distance_norm=math.hypot(cx - tx, cy - ty) / math.hypot(*size))


def draw_line(image, text, row=1, color=(60, 220, 255)):
    draw = ImageDraw.Draw(image)
    bottom = image.height - 8 - 26 * (row - 1)
    draw.rectangle([8, bottom - 22, min(image.width - 4, 12 + 6 * len(text)), bottom], fill=(0, 0, 0))
    draw.text((12, bottom - 18), text, fill=color)
    return text


def draw_alignment(image, alignment, tip):
    color = (60, 220, 255)
    ImageDraw.Draw(image).line([tuple(map(float, tip)), alignment['center']], fill=color, width=2)
    text = ('2D: fingertip inside can box (not touch)' if alignment['inside_box'] else
            f"2D: {alignment['center_distance_px']:.0f}px to centre, {alignment['edge_gap_px']:.0f}px to box")
    return draw_line(image, text, 1, color)
