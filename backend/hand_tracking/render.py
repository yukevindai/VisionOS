from __future__ import annotations

from PIL import Image, ImageDraw

from .tracker import CONNECTIONS, FINGERTIPS, TRACKING, HandFrame

COLORS = ((90, 220, 120), (90, 170, 255))
INDEX_COLOR = (255, 230, 60)


def draw_hands(image: Image.Image, result: HandFrame, frame_id=None) -> Image.Image:
    output = image.copy().convert('RGB')
    if result.status != TRACKING or (frame_id is not None and frame_id != result.frame_id):
        return output
    if result.image_size is not None and tuple(output.size) != tuple(result.image_size):
        return output
    draw = ImageDraw.Draw(output)
    radius = max(2, round(min(output.size) / 240))
    for hand in result.hands:
        color = COLORS[hand.track_id % len(COLORS)]
        points = [tuple(map(float, p)) for p in hand.landmarks_px]
        for a, b in CONNECTIONS:
            draw.line([points[a], points[b]], fill=color, width=max(1, radius // 2 + 1))
        for x, y in points:
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
        for name, index in FINGERTIPS.items():
            if name != 'INDEX':
                x, y = points[index]
                draw.ellipse([x - radius - 1, y - radius - 1, x + radius + 1, y + radius + 1], outline=color, width=2)
        x, y = points[8]
        ring = radius * 4
        draw.ellipse([x - ring, y - ring, x + ring, y + ring], outline=INDEX_COLOR, width=3)
        bx, by = hand.box_px[:2]
        draw.text((bx, max(0, by - 14)), f'hand {hand.track_id}', fill=color)
    return output
