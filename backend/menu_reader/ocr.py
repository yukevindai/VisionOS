"""Windows' installed OCR engine; no provider calls/models/global environment changes.

Experimental desktop adapter: Microsoft's documented deployment support requires MSIX identity.
This PowerShell interop works on the measured host; do not claim universal unpackaged support.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image, ImageOps


def _recognize(path: Path, language: str) -> tuple[dict, dict]:
    if sys.platform != 'win32':
        raise RuntimeError('Windows OCR requires Windows; use saved OCR JSON for parsing on other platforms')
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(Path(__file__).with_name('windows_ocr.ps1')), '-ImagePath', str(path.resolve()),
               '-Language', language]
    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8-sig', creationflags=subprocess.CREATE_NO_WINDOW)
    peak_rss = 0
    cpu_s = 0.
    try:
        import psutil
        monitor = psutil.Process(process.pid)
    except ImportError:
        monitor = None
    while True:
        if monitor:
            try:
                peak_rss = max(peak_rss, monitor.memory_info().rss)
                times = monitor.cpu_times()
                cpu_s = max(cpu_s, times.user+times.system)
            except Exception:
                pass
        try:
            stdout, stderr = process.communicate(timeout=.02)
            break
        except subprocess.TimeoutExpired:
            if time.perf_counter()-started > 60:
                process.kill()
                process.communicate()
                raise RuntimeError('Windows OCR timed out after 60 seconds')
    if process.returncode:
        raise RuntimeError('Windows OCR failed: '+stderr.strip()[:900])
    return json.loads(stdout), dict(wallMs=round((time.perf_counter()-started)*1000, 2),
        peakChildRssMB=round(peak_rss/(1024**2), 2) if peak_rss else None,
        childCpuSeconds=round(cpu_s, 3) if monitor else None)


def scan(path: Path, output: Path, language='en-US', coverage='unknown') -> dict:
    """Orient once, tile oversized images without downscaling, retain original coordinates."""
    started = time.perf_counter()
    data = path.read_bytes()
    page_id = hashlib.sha256(data).hexdigest()[:16]
    with Image.open(path) as source:
        original_size = source.size
        exif = int(source.getexif().get(274, 1) or 1)
        image = ImageOps.exif_transpose(source).convert('RGB')
    output.mkdir(parents=True, exist_ok=True)
    canonical = output/f'{page_id}-upright.png'
    image.save(canonical)
    # OcrEngine.MaxImageDimension is 10000 on this host. Use smaller 2400px tiles to bound
    # memory while preserving resolution. Overlap protects text near tile boundaries.
    edge, overlap = 2400, 240
    words, tiles, engines_ms, resources = [], [], [], []
    for y in range(0, image.height, edge-overlap):
        for x in range(0, image.width, edge-overlap):
            rect = (x, y, min(image.width, x+edge), min(image.height, y+edge))
            tile = image.crop(rect)
            tile_path = output/f'{page_id}-tile-{x}-{y}.png'
            tile.save(tile_path)
            result, usage = _recognize(tile_path, language)
            engines_ms.append(result['engineMs'])
            resources.append(usage)
            tiles.append(dict(cropPx=rect, ocr=result, resource=usage))
            for line in result['lines']:
                for word in line['words']:
                    box = [word['x']+x, word['y']+y, word['width'], word['height']]
                    duplicate = False
                    for existing in words:
                        a, b, w, h = existing['box']
                        intersection = max(0, min(a+w, box[0]+box[2])-max(a, box[0]))*max(0, min(b+h, box[1]+box[3])-max(b, box[1]))
                        if existing['text'] == word['text'] and intersection/max(1, min(w*h, box[2]*box[3])) > .6:
                            duplicate = True
                            break
                    if not duplicate:
                        words.append(dict(id=f'{page_id}-w{len(words)}', text=word['text'], box=box,
                                          confidence=None, pageId=page_id))
    return dict(pageId=page_id, source=str(path.resolve()), provenance='SAVED_IMAGE_LOCAL_OCR',
        engine='Windows.Media.Ocr', language=language, coverage=coverage,
        image=dict(width=image.width, height=image.height, originalSize=original_size,
                   exifOrientation=exif, orientationApplied=True, canonicalImage=str(canonical.resolve())),
        words=words, tiles=tiles, timing=dict(engineMs=round(sum(engines_ms), 2),
            wallMs=round((time.perf_counter()-started)*1000, 2),
            peakChildRssMB=max((r['peakChildRssMB'] or 0 for r in resources), default=0),
            childCpuSeconds=round(sum(r['childCpuSeconds'] or 0 for r in resources), 3)))
