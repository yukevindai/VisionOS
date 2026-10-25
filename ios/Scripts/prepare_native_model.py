#!/usr/bin/env python3
"""Export the teammate's hash-pinned YOLO11s model for native Core ML inference.

Run with the isolated exporter environment documented in ios/README.md.
No provider credentials are used. Downloaded/generated models remain ignored.
"""
import hashlib
import json
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'ios/Packages/Perception/Prototypes/DeskGuide-Laptop'
CACHE = ROOT / '.runtime/model-assets'
DEST = ROOT / 'ios/Resources/Models'


def main():
    from ultralytics import YOLO
    import coremltools
    import torch
    import ultralytics
    CACHE.mkdir(parents=True, exist_ok=True)
    DEST.mkdir(parents=True, exist_ok=True)
    asset = json.loads((SOURCE / 'models.lock.json').read_text())['yolo11s.pt']
    weights = CACHE / 'yolo11s.pt'
    if not weights.exists():
        request = urllib.request.Request(asset['url'], headers={'User-Agent': 'VisionOS-native-model/1.0'})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != asset['sha256']:
            raise RuntimeError('Upstream model hash changed; refusing replacement')
        weights.write_bytes(data)
    if hashlib.sha256(weights.read_bytes()).hexdigest() != asset['sha256']:
        raise RuntimeError('Local model hash differs from teammate manifest')
    exported = Path(YOLO(str(weights)).export(format='coreml', nms=True, imgsz=640,
                                             half=False, device='cpu'))
    compiled = CACHE / 'compiled'
    compiled.mkdir(exist_ok=True)
    subprocess.run(['xcrun', 'coremlcompiler', 'compile', str(exported), str(compiled)], check=True)
    model = compiled / 'yolo11s.mlmodelc'
    shutil.copytree(model, DEST / model.name, dirs_exist_ok=True)
    files = {str(p.relative_to(DEST / model.name)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((DEST / model.name).rglob('*')) if p.is_file()}
    record = {'source': asset, 'export': {'format': 'coreml', 'nms': True, 'imgsz': 640,
              'half': False, 'ultralytics': ultralytics.__version__, 'coremltools': coremltools.__version__,
              'torch': torch.__version__}, 'compiledFiles': files,
              'validation': 'Conversion only; device detection accuracy and latency not yet validated.'}
    (DEST / 'model-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Prepared', DEST / model.name)


if __name__ == '__main__':
    main()
