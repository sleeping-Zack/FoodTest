"""Query copies only: keep raw evidence and meaningful case/version differences."""
import re
import unicodedata

from food_kb.common import KBError

NORMALIZER_VERSION = 'retrieval-normalize-1'
_DASHES = str.maketrans({char: '-' for char in '‐‑‒–—−﹘﹣－'})
_STANDARD_FIELDS = {'designation', 'family', 'method_standard', 'judgment_standard'}
_IDENTITY_FIELDS = {'record_id', 'version_id', 'node_id', 'project_id', 'review_state',
                    'namespace', 'record_type'}


def normalize_text(value):
    """NFKC, dash normalization and whitespace removal, without case folding."""
    if not isinstance(value, str):
        raise KBError('QUERY_TYPE', '查询与索引文本必须是字符串。')
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).translate(_DASHES))


def normalize_standard(value):
    normalized = normalize_text(value)
    # Only the identifier prefix is case insensitive; chemical symbols and units
    # in arbitrary text are never upper/lower-cased by this normalizer.
    match = re.fullmatch(r'(GB(?:/T|T|/Z|Z)?|NY(?:/T|T)?|SN(?:/T|T)?|DB\d*(?:/T|T)?|'
                         r'ISO|CAC|JJF|JJG)(\d[\d.]*(?:-\d{4})?)', normalized, re.I)
    if match:
        prefix = match.group(1).upper()
        if '/' not in prefix and prefix.endswith(('T', 'Z')):
            prefix = prefix[:-1] + '/' + prefix[-1]
        return prefix + match.group(2)
    return normalized


def normalize_filter_value(key, value):
    if key == 'year' and isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise KBError('QUERY_FILTER', '精确过滤值必须是非空字符串，年份也可为四位整数。')
    if key == 'year':
        normalized = normalize_text(value)
        if not re.fullmatch(r'\d{4}', normalized):
            raise KBError('QUERY_YEAR', '标准年份必须是四位数字。')
        return normalized
    if key in _STANDARD_FIELDS:
        return normalize_standard(value)
    if key in _IDENTITY_FIELDS:
        return unicodedata.normalize('NFKC', value).strip()
    return normalize_text(value)


def normalized_spans(text, term):
    """Return actual raw slices; unusual cross-character NFKC gets field precision.

    Most Chinese/full-width normalization is character local. If Unicode
    composition crosses characters, report the actual whole field rather than
    inventing offsets. The caller can still verify its normalized phrase.
    """
    normalized = normalize_text(text)
    if not term or term not in normalized:
        return []
    pieces = []; offsets = []
    for position, char in enumerate(text):
        part = normalize_text(char)
        pieces.append(part)
        offsets.extend([position] * len(part))
    if ''.join(pieces) != normalized:
        return [{'span': [0, len(text)], 'matched_text': text, 'span_precision': 'field'}]
    result = []; start = 0
    while len(result) < 20:
        position = normalized.find(term, start)
        if position < 0:
            break
        left = offsets[position]; right = offsets[position + len(term) - 1] + 1
        result.append({'span': [left, right], 'matched_text': text[left:right],
                       'span_precision': 'exact'})
        start = position + max(1, len(term))
    return result
