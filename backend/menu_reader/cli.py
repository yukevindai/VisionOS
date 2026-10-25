"""Saved-photo menu OCR and grounded text questions; no live camera or speech dispatch.

python -m menu_reader.cli photo.jpg --out .runtime/menu-demo --interactive
"""
import argparse
import json
from pathlib import Path
import time

from .ocr import scan
from .layout import document
from .questions import MenuSession, reading_chunks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='*', type=Path)
    parser.add_argument('--ocr-json', nargs='+', type=Path, help='Parse previously saved page OCR without OCR/network')
    parser.add_argument('--out', type=Path, default=Path('.runtime/menu-demo'))
    parser.add_argument('--language', default='en-US')
    parser.add_argument('--coverage', choices=['unknown','partial'], default='unknown')
    parser.add_argument('--interactive', action='store_true')
    parser.add_argument('--question', action='append', default=[])
    args = parser.parse_args(argv)
    if not args.images and not args.ocr_json:
        parser.error('Supply one or more saved menu images or --ocr-json')
    started = time.perf_counter()
    pages = [json.loads(p.read_text(encoding='utf-8')) for p in args.ocr_json or []]
    try:
        pages.extend(scan(p, args.out/'captures', args.language, args.coverage) for p in args.images)
    except RuntimeError as error:
        parser.exit(2, str(error)+'\n')
    menu = document(pages)
    menu['timing'] = dict(totalStructuredMs=round((time.perf_counter()-started)*1000, 2),
                          firstReadableMs=pages[0].get('timing',{}).get('wallMs', pages[0].get('timing',{}).get('engineMs')),
                          replayedOCR=bool(args.ocr_json))
    args.out.mkdir(parents=True, exist_ok=True)
    for page in pages:
        (args.out/f"{page['pageId']}-ocr.json").write_text(json.dumps(page, indent=2), encoding='utf-8')
    (args.out/'menu.json').write_text(json.dumps(menu, indent=2), encoding='utf-8')
    readout='\n\n'.join(reading_chunks(menu))
    (args.out/'readout.txt').write_text(readout, encoding='utf-8')
    if args.interactive:
        print('Recognized sections: '+', '.join(menu['sections'])+'. Ask for a section or say "Read everything". Capture completeness is unverified.')
    else:
        print(readout)
    print('\nQuality: '+' '.join(menu['warnings']))
    print(f"\nSaved {len(menu['items'])} priced entries and {len(menu['unstructured'])} unresolved rows to {args.out.resolve()}")
    session = MenuSession(menu)
    for question in args.question:
        print(json.dumps(session.answer(question), ensure_ascii=False))
    if args.interactive:
        print('Ask about sections, dishes, cheapest, printed allergens; read everything; pause/resume/skip/next/stop. Type quit to exit. No audio is dispatched.')
        while True:
            try: question=input('You> ')
            except (EOFError, KeyboardInterrupt): break
            if question.strip().casefold() in ('quit','exit'): break
            print(json.dumps(session.answer(question), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
