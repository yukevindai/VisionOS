import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parent


def main():
    manifest=json.loads((ROOT/'models.lock.json').read_text())
    folder=ROOT/'models'
    folder.mkdir(exist_ok=True)
    for name,asset in manifest.items():
        if name not in {"hand_landmarker.task", "MEDIAPIPE-LICENSE", "yolo11s.pt"}:
            continue
        path=folder/name
        digest=lambda data:hashlib.sha256(data).hexdigest()
        if path.exists() and digest(path.read_bytes())==asset['sha256']:
            print(name,'verified')
            continue
        print('Downloading',name,flush=True)
        request=urllib.request.Request(asset['url'],headers={'User-Agent':'Reflex/1.0'})
        with urllib.request.urlopen(request,timeout=90) as response:
            data=response.read()
        if digest(data)!=asset['sha256']:
            raise RuntimeError(name+': upstream content changed; refusing an unverified asset')
        path.write_bytes(data)
    print('Model and license hashes verified.')


if __name__=='__main__': main()
