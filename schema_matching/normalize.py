"""
Name normalization for fuzzy schema matching across sources.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Substring boost only when the shorter token is substantial (avoids id ↔ cust_id)
_MIN_SUBSTRING_LEN = 3
_MIN_SUBSTRING_RATIO = 0.5


def normalize_identifier(name: str) -> str:
    """Lowercase and strip common separators. No plural stripping (avoids address → addres)."""
    if not name:
        return ""
    s = name.strip().lower()
    return re.sub(r"[_\-\s]+", "", s)


def name_similarity(a: str, b: str) -> float:
    """Return 0–1 similarity between two identifiers."""
    na, nb = normalize_identifier(a), normalize_identifier(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if (
        len(shorter) >= _MIN_SUBSTRING_LEN
        and shorter in longer
        and len(shorter) / len(longer) >= _MIN_SUBSTRING_RATIO
    ):
        return 0.92

    return SequenceMatcher(None, na, nb).ratio()


def pick_canonical_name(names: list[str], *, prefer: list[str] | None = None) -> str:
    """
    Choose a representative name from a cluster.

    If ``prefer`` is given, pick the first preferred name present in ``names``;
    otherwise prefer the longest normalized form (stable tie-break on lowercase).
    """
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if prefer:
        for p in prefer:
            for n in names:
                if n.lower() == p.lower():
                    return n
    return max(names, key=lambda n: (len(normalize_identifier(n)), n.lower()))
