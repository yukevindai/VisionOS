"""Compare candidates BEFORE altering production defaults. No camera, Gemini, audio or models."""
import argparse
import json
from pathlib import Path
import time

from PIL import Image, ImageOps

from .ocr import scan
from .preprocessing import PROFILES, prepare
from .layout import document


def scan_candidate(source, output, profile):
    if profile=='native':
        return scan(source, output)
    started=time.perf_counter()
    with Image.open(source) as raw:
        original_size=raw.size
        exif=int(raw.getexif().get(274,1) or 1)
        upright=ImageOps.exif_transpose(raw).convert('RGB')
    transformed, mapping=prepare(upright,profile)
    output.mkdir(parents=True,exist_ok=True)
    recognition=output/'recognition-input.png'
    canonical=output/'source-upright.png'
    transformed.save(recognition);upright.save(canonical)
    page=scan(recognition,output/'ocr')
    for word in page['words']:
        x,y,w,h=word['box']
        word['box']=[x/mapping['scaleX'],y/mapping['scaleY'],w/mapping['scaleX'],h/mapping['scaleY']]
    for tile in page['tiles']:
        tile['coordinateSystem']='recognitionImagePixels'
    page['source']=str(source.resolve())
    page['image'].update(width=upright.width,height=upright.height,originalSize=original_size,
        exifOrientation=exif,canonicalImage=str(canonical.resolve()),recognitionImage=str(recognition.resolve()))
    page['preprocessing']=mapping
    page['timing']['wallMs']=round((time.perf_counter()-started)*1000,2)
    return page


def run(images,output,profiles):
    results=[]
    for source in images:
        for profile in profiles:
            target=output/source.stem/profile
            page=scan_candidate(source,target,profile)
            menu=document([page])
            target.mkdir(parents=True,exist_ok=True)
            (target/'ocr.json').write_text(json.dumps(page,indent=2),encoding='utf-8')
            (target/'menu.json').write_text(json.dumps(menu,indent=2),encoding='utf-8')
            row=dict(image=str(source.resolve()),profile=profile,engineMs=page['timing']['engineMs'],
                wallMs=page['timing']['wallMs'],words=len(page['words']),pricedItems=len(menu['items']),
                pageId=page['pageId'],ocrFile=str((target/'ocr.json').resolve()))
            results.append(row)
            (output/'comparison-unscored.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
            print(source.stem,profile,'words',row['words'],'items',row['pricedItems'],'ms',row['wallMs'],flush=True)
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images',nargs='+',type=Path)
    parser.add_argument('--profiles',nargs='+',choices=PROFILES,default=list(PROFILES))
    parser.add_argument('--out',type=Path,default=Path('.runtime/menu-real/comparison'))
    args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    run(args.images,args.out,args.profiles)
