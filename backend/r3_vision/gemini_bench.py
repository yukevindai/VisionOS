"""Benchmark Gemini configurations on saved PathOn captures (real API calls; the key is read, never printed).

Reference boxes come from each capture's saved Gemini result (checked visually). Scored per request: SELECTED with a
box overlapping the reference (IoU >= 0.5); AMBIGUOUS when two references exist (two bagels); NOT_FOUND when the
object is absent. Writes .runtime/gemini-bench/report.json and prints a table.

    python -m r3_vision.gemini_bench --max-calls 120
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
import io
import json
import os
import statistics
import time
from pathlib import Path

from PIL import Image

from . import gemini, object_select, pipeline, target_query
from .cli import client_from_args

BACKEND = Path(__file__).resolve().parents[1]
CAPTURES = BACKEND / 'recordings/r3-tracking'
REQUESTS = [
    ('Find the Red Bull can', ['red bull']),
    ('Find the Pepsi can', ['pepsi']),
    ('Find the black Owala tumbler', ['owala', 'black water bottle', 'black bottle']),
    ('Find the cup of coffee', ['coffee']),
    ('Find the deck of cards', ['card', 'bicycle']),
    ('Find the bagel', ['bagel']),
    ('Find the banana', []),
]
CONFIGS = {
    'inventory 3.8-flash low, image medium (current)': ('inventory', 'gemini-3.8-flash', 'low', 'medium'),
    'inventory 3.8-flash low, image low': ('inventory', 'gemini-3.8-flash', 'low', 'low'),
    'inventory 3.8-flash low, image default': ('inventory', 'gemini-3.8-flash', 'low', ''),
    'target 3.8-flash low, image medium': ('target', 'gemini-3.8-flash', 'low', 'medium'),
    'target 3.8-flash low, image low': ('target', 'gemini-3.8-flash', 'low', 'low'),
    'target 3.6-flash minimal, image medium': ('target', 'gemini-3.6-flash', 'minimal', 'medium'),
    'target 3.5-flash-lite minimal, image medium': ('target', 'gemini-3.5-flash-lite', 'minimal', 'medium'),
}


def iou(a, b):
    ax, ay, aw, ah = (a[k] for k in ('x', 'y', 'width', 'height'))
    bx, by, bw, bh = (b[k] for k in ('x', 'y', 'width', 'height'))
    inter = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.


def frames(limit):
    out = []
    for path in sorted(CAPTURES.glob('track-*/result.json'), key=os.path.getmtime, reverse=True):
        result = json.loads(path.read_text(encoding='utf-8'))
        controls = (result.get('screenModel') or {}).get('controls') or []
        labels = ' '.join(c.get('label', '').lower() for c in controls)
        # References need a full inventory: captures listing <= 8 objects may come from the capped prompt.
        if (not result.get('provider', {}).get('ok') or len(controls) < 9
                or sum(any(k in labels for k in keys) for _, keys in REQUESTS[:5]) < 3):
            continue
        image = path.with_name('captured.png')
        if image.exists():
            out.append((path.parent.name[6:14], image, controls))
        if len(out) >= limit:
            break
    return out


def expected(controls, keys):
    refs = [c['region'] for c in controls if c.get('region') and any(k in c.get('label', '').lower() for k in keys)]
    return ('NOT_FOUND' if not refs else 'AMBIGUOUS' if len(refs) > 1 else 'SELECTED'), refs


def one(client, kind, jpeg, request, frame_id):
    if kind == 'target':
        result = target_query.run(jpeg, request, client, frame_id=frame_id)
    else:
        asker = copy.copy(client)
        asker.prompt = gemini.object_prompt(request)
        result = pipeline.run(jpeg, request, asker, frame_id=frame_id)
        if result.get('screenModel'):
            result['selection'] = object_select.select(request, result['screenModel'])
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--frames', type=int, default=3)
    p.add_argument('--max-calls', type=int, default=150)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--configs', nargs='*', help='Subset of configuration names (default: all)')
    p.add_argument('--timeout', type=float, default=30)
    a = p.parse_args(argv)
    base = client_from_args(None)
    if not base.configured:
        raise SystemExit('Set GEMINI_API_KEY (environment or backend/.env) to run real API calls; nothing was sent.')
    shots = frames(a.frames)
    if not shots:
        raise SystemExit('No saved captures with enough reference objects in recordings/r3-tracking.')
    encoded = {}
    for frame_id, image_path, _ in shots:
        buffer = io.BytesIO()
        Image.open(image_path).convert('RGB').save(buffer, 'JPEG', quality=95)
        encoded[frame_id] = buffer.getvalue()
    names = a.configs or list(CONFIGS)
    jobs = [(name, frame_id, controls, request, keys) for name in names for frame_id, _, controls in shots
            for request, keys in REQUESTS][:a.max_calls]
    print(f'{len(jobs)} real Gemini calls on {len(shots)} saved frames, {a.workers} at a time', flush=True)

    def run(job):
        name, frame_id, controls, request, keys = job
        kind, model, thinking, media = CONFIGS[name]
        client = copy.copy(base)
        client.model, client.thinking_level, client.media_resolution, client.timeout_s = model, thinking, media, a.timeout
        started = time.perf_counter()
        result = one(client, kind, encoded[frame_id], request, frame_id)
        wall = time.perf_counter() - started
        want, refs = expected(controls, keys)
        selection = result.get('selection', {})
        got = selection.get('status')
        correct = got == want
        if correct and want == 'SELECTED':
            correct = iou(selection['target']['imageRegion'], refs[0]) >= .5
        error = (result.get('provider') or {}).get('error')
        return dict(config=name, frame=frame_id, request=request, expected=want, got=got, correct=bool(correct),
                    providerS=round(result.get('timingMs', {}).get('provider', 0) / 1000, 2), wallS=round(wall, 2),
                    label=(selection.get('target') or {}).get('label'), error=error and error.get('kind'))
    rows = []
    with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
        for row in pool.map(run, jobs):
            rows.append(row)
            print(f"{row['config'][:46]:46s} {row['request'][9:31]:22s} {row['got'] or '-':20s} "
                  f"{'OK ' if row['correct'] else 'BAD'} {row['providerS']:5.1f}s {row['error'] or ''}", flush=True)
    summary = {}
    for name in names:
        r = [x for x in rows if x['config'] == name]
        if not r:
            continue
        latencies = sorted(x['providerS'] for x in r if not x['error'])
        summary[name] = dict(calls=len(r), correct=sum(x['correct'] for x in r),
                             wrongSelections=sum(1 for x in r if x['got'] == 'SELECTED' and not x['correct']),
                             errors=sum(1 for x in r if x['error']),
                             p50=latencies and round(statistics.median(latencies), 2),
                             p95=latencies and round(latencies[min(len(latencies) - 1, int(.95 * len(latencies)))], 2))
    out = BACKEND / '.runtime/gemini-bench'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(dict(summary=summary, rows=rows), indent=1))
    print('\nconfiguration                                    correct  wrong-lock  errors   p50     p95')
    for name, s in summary.items():
        print(f"{name[:48]:48s} {s['correct']:3d}/{s['calls']:<3d}  {s['wrongSelections']:5d}  {s['errors']:6d}  "
              f"{s['p50'] or 0:5.1f}s  {s['p95'] or 0:5.1f}s")
    print('Time to verified lock ~= p50 + ~0.6 s capture/lock overhead (measured live). Report:', out / 'report.json')


if __name__ == '__main__':
    main()
