"""Measure REAL OCR on SIMULATED artwork. No API calls; no live-camera claims."""
from collections import Counter
import argparse
import json
from pathlib import Path
import re
import time

from .fixtures import create
from .layout import document
from .ocr import scan
from .questions import MenuSession


def normalize(text):
    return ' '.join(text.casefold().split())


def distance(a, b):
    row = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        next_row = [i]
        for j, y in enumerate(b, 1):
            next_row.append(min(next_row[-1]+1, row[j]+1, row[j-1]+(x!=y)))
        row = next_row
    return row[-1]


def evaluate(root):
    cases = create(root/'images')
    (root/'truth.json').write_text(json.dumps(cases,indent=2),encoding='utf-8')
    results = []
    menus = {}
    for case in cases:
        started = time.perf_counter()
        page = scan(Path(case['image']), root/case['name'], coverage=case['coverage'])
        page['provenance'] = case['provenance']
        menu = document([page])
        structured_ms = (time.perf_counter()-started)*1000
        menus[case['name']] = menu
        recognized = [line['text'] for line in menu['parsedPages'][0]['lines']]
        a, b = normalize(' '.join(case['expectedRows'])), normalize(' '.join(recognized))
        expected_words, actual_words = Counter(a.split()), Counter(b.split())
        common = sum((expected_words & actual_words).values())
        expected = {(i['name'].casefold(), i['price']) for i in case['items']}
        actual = {(i['name'].casefold(), i['price']['raw']) for i in menu['items']}
        correct_groups = sum(any(i['name'].casefold()==truth['name'].casefold() and i['price']['raw']==truth['price']
                                 and i['section']==truth['section'] for i in menu['items']) for truth in case['items'])
        result = dict(name=case['name'], provenance=case['provenance'],
            characterErrorRate=round(distance(a,b)/max(1,len(a)),4),
            tokenPrecision=round(common/max(1,sum(actual_words.values())),4),
            tokenRecall=round(common/max(1,sum(expected_words.values())),4),
            correctNamePricePairs=len(expected & actual), expectedNamePricePairs=len(expected),
            missingPairs=sorted(expected-actual), unexpectedPairs=sorted(actual-expected),
            correctSectionGroups=correct_groups,
            firstReadableMs=page['timing']['wallMs'], structuredMs=round(structured_ms,2),
            engineMs=page['timing']['engineMs'], peakChildRssMB=page['timing']['peakChildRssMB'],
            childCpuSeconds=page['timing']['childCpuSeconds'], warnings=menu['warnings'])
        (root/case['name']/'ocr.json').write_text(json.dumps(page,indent=2),encoding='utf-8')
        (root/case['name']/'menu.json').write_text(json.dumps(menu,indent=2),encoding='utf-8')
        results.append(result)
        print(case['name'],json.dumps({k:result[k] for k in ['engineMs','firstReadableMs','correctNamePricePairs','expectedNamePricePairs','characterErrorRate']}),flush=True)
    session = MenuSession(menus['printed_menu'])
    qa = []
    for query, wanted, status in [
        ('What sections are there?', 'MAINS', 'GROUNDED'),
        ('What mains do they have?', 'Peanut Noodles', 'OCR_UNVERIFIED'),
        ('What is the cheapest one?', 'Peanut Noodles', 'PROVISIONAL'),
        ('Does Garden Pasta contain peanuts?', 'does not confirm', 'ABSTAIN'),
        ('Does Peanut Noodles contain peanuts?', 'Contains peanuts.', 'PRINTED_INFORMATION_ONLY'),
        ('Help me communicate with the cashier', 'Confirm before speaking', 'DRAFT'),
        ('Read everything', 'Iced Tea', 'CAPTURED_TEXT_ONLY')]:
        started=time.perf_counter()
        answer=session.answer(query)
        qa.append(dict(question=query,answer=answer,correct=wanted in answer['text'] and answer['status']==status,
                       elapsedMs=round((time.perf_counter()-started)*1000,3)))
    report = dict(evaluation='CONTROLLED_SYNTHETIC_IMAGES_WITH_REAL_WINDOWS_OCR_NOT_LIVE',
        cases=results,qa=qa,qaCorrect=sum(q['correct'] for q in qa),qaTotal=len(qa),
        notes=['Single run per case; not a statistical p95 or real-menu field validation.',
               'Character error normalizes case/whitespace; pairing and grouping require exact expected names/prices.',
               'Unexpected pairs are parser/OCR mistakes, not proof of generative hallucination.',
               'PowerShell child RSS/CPU are sampled; do not represent total system memory or battery.',
               'First readable result is the completed OCR call; this engine does not stream individual lines.'])
    (root/'benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('.runtime/menu-eval'))
    args=parser.parse_args()
    report=evaluate(args.out)
    print('Grounded QA:',report['qaCorrect'],'/',report['qaTotal'])
