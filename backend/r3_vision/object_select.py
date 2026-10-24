"""Object-mode selection on top of the shared selector, which stays in parity with the Swift TargetSelector.

Only one extra rule: when the shared selector finds no match, a request whose words written together equal a
label's words written together ("redbull" / "Red Bull") is retried as that label. The shared selector still
validates the result, so duplicates stay AMBIGUOUS and invalid boxes stay NOT_FOUND.
"""
from . import selector


def _joined(words):
    return ''.join(words)


def select(request, model):
    result = selector.select(request, model)
    if result.get('status') != 'NOT_FOUND' or result.get('reason') not in ('NO_MATCH', 'NO_MATCH_PARTIAL_VIEW'):
        return result
    wanted = _joined(selector.content_tokens(request))
    if not wanted:
        return result
    labels = {}
    for control in model.get('controls', []):
        words = selector.content_tokens(control.get('label', ''))
        if words and wanted in _joined(words) and len(wanted) >= 4:
            labels.setdefault(' '.join(words), []).append(control)
    if len(labels) != 1:
        return result
    retried = selector.select(next(iter(labels)), model)
    retried['request'] = request
    retried['match'] = 'joinedWords'
    return retried


def candidates(result, model):
    ids = set(result.get('candidateIds') or [])
    return [c.get('label', '') for c in model.get('controls', []) if c.get('id') in ids]
