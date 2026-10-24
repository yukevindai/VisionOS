"""Pinned official OpenCV Zoo ViTTrack asset; never downloads during tracking."""
import hashlib
from pathlib import Path
import urllib.request

MODEL_SHA256 = '2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d'
MODEL_SIZE = 714726
MODEL_PATH = Path(__file__).resolve().parents[1]/'.runtime/models/object_tracking_vittrack_2023sep.onnx'
MODEL_URL = 'https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/object_tracking_vittrack/object_tracking_vittrack_2023sep.onnx'
LICENSE_URL = 'https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/object_tracking_vittrack/LICENSE'


def ensure_model(download=False):
    if MODEL_PATH.exists():
        if hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError('ViTTrack model checksum mismatch; remove the corrupt local model and download again')
        return MODEL_PATH
    if not download:
        raise FileNotFoundError('ViTTrack model missing. Run: python -m r3_vision.tracker_models')
    with urllib.request.urlopen(MODEL_URL, timeout=30) as response:
        data = response.read(MODEL_SIZE+1)
    if len(data) != MODEL_SIZE or hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError('ViTTrack download failed pinned size/SHA-256 verification')
    with urllib.request.urlopen(LICENSE_URL, timeout=15) as response:
        license_data = response.read(50000)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.with_name('VITTRACK_LICENSE').write_bytes(license_data)
    MODEL_PATH.write_bytes(data)
    return MODEL_PATH


if __name__ == '__main__':
    ensure_model(download=True)
    print('OpenCV ViTTrack model ready; pinned SHA-256 verified (714726 bytes).')
