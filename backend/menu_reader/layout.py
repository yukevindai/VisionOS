"""Conservative layout baseline: exact OCR wording, same-row prices, explicit uncertainty.

This is not a trained universal menu parser. Unsupported layouts remain unstructured text.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import statistics
import math

MONEY = re.compile(r'(?<!\w)(?:[$€£]\s*\d+(?:,\d{3})*(?:\.\d{2})?|\d+\.\d{2})(?!\w)')
NOTE = re.compile(r'\b(contains?|allergens?|ingredients?|vegan|vegetarian|gluten.free|dairy.free|peanuts?|nuts?|milk|soy|sesame|eggs?)\b', re.I)


def _union(words):
    left = min(w['box'][0] for w in words)
    top = min(w['box'][1] for w in words)
    right = max(w['box'][0]+w['box'][2] for w in words)
    bottom = max(w['box'][1]+w['box'][3] for w in words)
    return [left, top, right-left, bottom-top]


def _rows(words):
    rows = []
    for word in sorted(words, key=lambda w: (w['box'][1]+w['box'][3]/2, w['box'][0])):
        center = word['box'][1]+word['box'][3]/2
        chosen = next((r for r in reversed(rows) if abs(r['center']-center) <= max(4, word['box'][3]*.45)), None)
        if chosen is None:
            rows.append(dict(center=center, words=[word]))
        else:
            chosen['words'].append(word)
            chosen['center'] = statistics.mean(w['box'][1]+w['box'][3]/2 for w in chosen['words'])
    return [dict(text=' '.join(w['text'] for w in sorted(r['words'], key=lambda w: w['box'][0])),
                 box=_union(r['words']), words=sorted(r['words'], key=lambda w: w['box'][0])) for r in rows]


def _column_cut(words, width):
    # Detect a gutter crossed by few ALPHABETIC words, with repeated text starting on both
    # sides. Price-only columns cannot create a second menu column.
    alphabetic = [w for w in words if any(c.isalpha() for c in w['text']) and not MONEY.fullmatch(w['text'])]
    candidates = []
    for fraction in [.40, .45, .50, .55, .60]:
        cut = width*fraction
        left = [w for w in alphabetic if w['box'][0]+w['box'][2] < cut-25]
        right = [w for w in alphabetic if w['box'][0] > cut+25]
        crosses = [w for w in alphabetic if w['box'][0] < cut+25 and w['box'][0]+w['box'][2] > cut-25]
        if len(left) >= 5 and len(right) >= 5 and len(crosses) <= 1:
            # A true right column has a repeated left edge, not isolated words at the end
            # of long descriptions. Require at least three rows near its first text edge.
            start = min(w['box'][0] for w in right)
            starts = [w for w in right if w['box'][0]-start < 50]
            if len(starts) >= 3:
                candidates.append((len(crosses), abs(fraction-.5), cut))
    return min(candidates)[2] if candidates else None


def parse_page(page):
    words = [w for w in page['words'] if isinstance(w.get('text'), str) and isinstance(w.get('box'), list)
             and len(w['box'])==4 and all(isinstance(n,(int,float)) and math.isfinite(n) for n in w['box'])
             and w['box'][0]>=0 and w['box'][1]>=0 and w['box'][2]>0 and w['box'][3]>0
             and w['box'][0]+w['box'][2]<=page['image']['width']
             and w['box'][1]+w['box'][3]<=page['image']['height']]
    if not words:
        return dict(title=None, items=[], sections=[], unstructured=[], warnings=['No readable text'], lines=[])
    width, height = page['image']['width'], page['image']['height']
    cut = _column_cut(words, width)
    columns = [[w for w in words if w['box'][0]+w['box'][2]/2 < cut],
               [w for w in words if w['box'][0]+w['box'][2]/2 >= cut]] if cut else [words]
    all_lines = _rows(words)
    first = all_lines[0]
    title = first['text'] if first['box'][1] < height*.13 and ('menu' in first['text'].casefold() or
        first['box'][3] > statistics.median(r['box'][3] for r in all_lines)*1.35) else None
    title_ids = {w['id'] for w in first['words']} if title else set()
    items, sections, unstructured, warnings = [], [], [], list(page.get('warnings',[]))
    if len(words)!=len(page['words']):
        warnings.append('Some OCR tokens have invalid/unlocated boxes; original OCR remains in page evidence.')
    if page.get('coverage') != 'complete':
        warnings.append('Only visible text was scanned; completeness of the menu/pages is unverified.')
    if page.get('coverage') == 'partial':
        warnings.append('Capture was explicitly marked partial.')
    if any(min(w['box'][0], w['box'][1], width-w['box'][0]-w['box'][2], height-w['box'][1]-w['box'][3]) < 5 for w in words):
        warnings.append('Text touches an image edge; content may be clipped.')
    if statistics.median(w['box'][3] for w in words) < 14:
        warnings.append('Small text: closer/higher-resolution capture recommended.')
    warnings.append('Independent word confidence is unavailable; recognized text/prices require review.')
    for column in columns:
        section, current = 'Uncategorized', None
        for line in _rows(column):
            text = line['text']
            if text == title or title_ids and all(w['id'] in title_ids for w in line['words']):
                continue
            prices = list(MONEY.finditer(text))
            ids = [w['id'] for w in line['words']]
            upper = any(c.isalpha() for c in text) and text.upper() == text
            if not prices and upper and len(text) <= 55 and not NOTE.search(text):
                section = text
                if section not in sections:
                    sections.append(section)
                current = None
                continue
            if prices:
                # A row with multiple prices may be size variants. Do not guess which price
                # belongs to a name. Preserve the exact row for review instead.
                if len(prices) != 1:
                    unstructured.append(dict(text=text, sourceIds=ids, reason='multiple_prices_or_variants'))
                    current = None
                    continue
                match = prices[0]
                name = text[:match.start()].strip()
                # A price-only row cannot be assigned vertically to the nearest item.
                if not name or text[match.end():].strip():
                    unstructured.append(dict(text=text, sourceIds=ids, reason='price_not_uniquely_associated'))
                    current = None
                    continue
                if name.casefold() in {'total','subtotal','tax','service charge','gratuity','delivery fee',
                                       'checkout','pay','payment','balance','amount due','cash','card'}:
                    unstructured.append(dict(text=text, sourceIds=ids, reason='transaction_line_not_menu_item'))
                    current = None
                    continue
                raw_price = match.group().strip()
                try:
                    value = str(Decimal(re.sub(r'[$€£,\s]', '', raw_price)))
                except InvalidOperation:
                    unstructured.append(dict(text=text, sourceIds=ids, reason='invalid_price'))
                    current = None
                    continue
                current = dict(id=f"{page['pageId']}-item{len(items)}", name=name, section=section,
                    description=[], printedNotes=[], variants=[], price=dict(raw=raw_price, amount=value,
                        currency=raw_price[0] if raw_price[0] in '$€£' else None),
                    sourceIds=ids, pageIds=[page['pageId']], box=line['box'], reviewed=False,
                    nameLineHeight=line['box'][3],
                    uncertainty=['OCR text and price unverified; confidence unavailable'])
                items.append(current)
                continue
            if current is not None:
                previous_bottom = current['box'][1]+current['box'][3]
                is_note = NOTE.search(text)
                is_description = text.endswith(('.', ';')) or line['box'][3] < current['nameLineHeight']*.9
                if line['box'][1]-previous_bottom < max(100, line['box'][3]*3) and not upper and (is_note or is_description):
                    field = 'printedNotes' if is_note else 'description'
                    current[field].append(dict(text=text, sourceIds=ids))
                    current['sourceIds'].extend(ids)
                    # Track the final associated line so multi-line descriptions remain adjacent.
                    current['box'][3] = line['box'][1]+line['box'][3]-current['box'][1]
                    continue
                current = None
            unstructured.append(dict(text=text, sourceIds=ids, reason='unpriced_or_unresolved_text'))
    return dict(title=title, items=items, sections=sections, unstructured=unstructured,
                warnings=warnings, lines=[dict(text=r['text'], box=r['box'], sourceIds=[w['id'] for w in r['words']]) for r in all_lines])


def document(pages):
    parsed = [parse_page(p) for p in pages]
    items, seen = [], {}
    for result in parsed:
        for item in result['items']:
            key = (result['title'], item['section'].casefold(), item['name'].casefold(), item['price']['raw'],
                   tuple(d['text'] for d in item['description']), tuple(n['text'] for n in item['printedNotes']))
            if key in seen:
                original = seen[key]
                original['sourceIds'] = list(dict.fromkeys(original['sourceIds']+item['sourceIds']))
                original['pageIds'] = list(dict.fromkeys(original['pageIds']+item['pageIds']))
                original['uncertainty'] = list(dict.fromkeys(original['uncertainty']+['Exact repeated entry merged across captures; verify it is the same menu entry']))
            else:
                items.append(item)
                seen[key] = item
    titles = {p['title'].casefold() for p in parsed if p['title']}
    warnings=list(dict.fromkeys(w for p in parsed for w in p['warnings']))
    if len(titles)>1: warnings.append('Conflicting menu titles across photos; confirm they belong to the same menu before comparing prices.')
    return dict(schemaVersion=1, kind='menuCandidate' if items else 'textOnly',
        hasTitleConflict=len(titles)>1,
        complete=False, title=next((p['title'] for p in parsed if p['title']), None),
        sections=list(dict.fromkeys(s for p in parsed for s in p['sections'])), items=items,
        unstructured=[u for p in parsed for u in p['unstructured']],
        warnings=warnings,
        pages=pages, parsedPages=parsed, sourcePolicy='Facts reference exact OCR spans; no inferred ingredients, safety or actions')
