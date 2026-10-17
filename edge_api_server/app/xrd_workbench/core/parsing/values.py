"""Shared text normalisation and numeric-token reading for every parser."""

import math
import re
import unicodedata


def normal(value):
    """NFC-normalise text and collapse whitespace runs; None becomes ''."""
    return ' '.join(unicodedata.normalize('NFC', value or '').split())


def central(raw):
    """Central value of a numeric token such as '12.34(5)', or None unless finite.

    The parenthesised uncertainty is not interpreted; callers keep the raw token."""
    value = normal(raw)
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:\(\d+\))?', value):
        return None
    number = float(value.split('(')[0])
    return number if math.isfinite(number) else None


def pdf_number(raw):
    """Read a single PDF numeric cell with soft line wraps; keep ordinary spaces strict.

    Raw tokens stay untouched in the report. This is deliberately not used by the
    TXT reader or manual-value validation, where joining tokens could change data.
    """
    return central(re.sub(r'[ \t]*[\r\n]+[ \t]*', '', raw or ''))
