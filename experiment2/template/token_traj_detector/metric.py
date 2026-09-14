"""Token-trajectory anomaly detector.

Input is a generated token-id sequence. Script / glyph rules also need each
token decoded to text (pass ``token_texts`` or a ``decode`` callback).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Public result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetectResult:
    is_ill: bool
    ill_type: int  # 0 normal · 1 script/glyph · 3 repetition
    reason: str    # rule name, or "" if not flagged


# Qwen3 special ids used only by exact_loop end-token stripping.
DEFAULT_END_TOKEN_IDS: Tuple[int, ...] = (151643, 151645, 151646, 151647)
_QWEN3_IM_END = 151645

_SPLICE_SCRIPTS = frozenset({
    "cyrillic", "arabic", "korean_hangul", "thai",
    "japanese_hiragana", "devanagari", "control_bytes",
})
_WORD_CATS = frozenset({"english_latin", "english_latin_space", "numbers"})
_FFFD_CATS = frozenset({"english_latin", "english_latin_space", "numbers", "punctuation"})
_CJK_WORD_CATS = frozenset({"english_latin", "english_latin_space"})
_TE_SEPS = frozenset({".", ")", ":"})

# exact_loop / block rules
_EL_MIN_PERIOD = 1
_EL_MIN_LOOP_TOKENS = 45
_EL_MIN_COPIES = 1.1
_EL_STRIP_END = 4
_EL_NATURAL_END_MIN_LOOP = 300
_BR_BLOCK, _BR_MIN = 64, 11
_PR_WINDOW, _PR_BLOCK, _PR_MIN = 640, 64, 5
_BLR_BLOCK, _BLR_MIN = 1024, 3
_TE_MIN_ITEMS, _TE_MIN_BODY = 6, 8
_SR_MIN_WORDS, _SR_MIN_TOTAL, _SR_MIN_PACKED, _SR_WINDOW = 15, 5, 4, 800
_ISO_MAX_COUNT, _ISO_MAX_RUN = 2, 1
_CJK_MIN_COUNT, _CJK_MAX_COUNT, _CJK_MAX_RUN = 1, 3, 1
_GR_MIN_REPEATS, _GR_MIN_COUNT, _GR_MAX_DISTINCT = 2, 5, 2


# ---------------------------------------------------------------------------
# Token-text → category (same rules as the original token2category generator)
# ---------------------------------------------------------------------------

_SCRIPT_LABELS = {
    "cjk": "chinese_cjk",
    "hiragana": "japanese_hiragana",
    "katakana": "japanese_katakana",
    "hangul": "korean_hangul",
    "thai": "thai",
    "greek": "greek",
    "variation_selector": "variation_selector",
    "latin": "english_latin",
    "latin_space": "english_latin_space",
    "digit": "numbers",
    "emoji": "emoji",
    "whitespace": "whitespace",
    "punct": "punctuation",
    "symbol": "symbol",
    "control": "control",
    "arabic": "arabic",
    "cyrillic": "cyrillic",
    "devanagari": "devanagari",
    "math_letter": "mathematics",
    "modifier_letter": "mathematics",
    "fraction": "mathematics",
}
_WHITESPACE_CHARS = set(" \t\n\r\f\v▁ĠĊ█")


@lru_cache(maxsize=4096)
def _classify_char(ch: str) -> str:
    if ch in _WHITESPACE_CHARS:
        return "whitespace"
    codepoint = ord(ch)
    if 0x1F300 <= codepoint <= 0x1FAFF:
        return "emoji"
    if ch.isdigit():
        return "digit"
    name = unicodedata.name(ch, "")
    if not name:
        category = unicodedata.category(ch)
        if category.startswith("C"):
            return "control"
        if category.startswith("P"):
            return "punct"
        if category.startswith("S"):
            return "symbol"
        return "other"
    name_upper = name.upper()
    if "PLANCK CONSTANT" in name_upper or "MATHEMATICAL" in name_upper or "DOUBLE-STRUCK CAPITAL" in name_upper:
        return "math_letter"
    if "MODIFIER LETTER" in name_upper:
        return "modifier_letter"
    if "SPACE" in name_upper or unicodedata.category(ch) in {"Zs", "Zl", "Zp"}:
        return "whitespace"
    if "CJK UNIFIED IDEOGRAPH" in name_upper or "CJK COMPATIBILITY" in name_upper:
        return "cjk"
    if "HIRAGANA" in name_upper:
        return "hiragana"
    if "HANGUL" in name_upper:
        return "hangul"
    if "THAI" in name_upper:
        return "thai"
    if "ARABIC" in name_upper:
        return "arabic"
    if "CYRILLIC" in name_upper:
        return "cyrillic"
    if "DEVANAGARI" in name_upper:
        return "devanagari"
    if "LATIN" in name_upper:
        return "latin"
    if "VARIATION SELECTOR" in name_upper:
        return "variation_selector"
    category = unicodedata.category(ch)
    if category.startswith("P"):
        return "punct"
    if category.startswith("S"):
        return "symbol"
    if category.startswith("C"):
        return "control"
    return "other"


@lru_cache(maxsize=65536)
def categorize_text(decoded: str) -> str:
    """Map one decoded token string to a script / word-shape category."""
    char_counts: Counter = Counter()
    printable = 0
    for char in decoded:
        char_class = _classify_char(char)
        char_counts[char_class] += 1
        if char_class != "control":
            printable += 1

    total_chars = sum(char_counts.values()) or 1
    printable_fraction = printable / total_chars
    dominant, dom_count = char_counts.most_common(1)[0] if char_counts else ("other", 0)
    dominant_ratio = dom_count / total_chars

    label = _SCRIPT_LABELS.get(dominant, "other")
    if dominant == "latin" and printable_fraction > 0.8:
        label = "english_latin_space" if "whitespace" in char_counts else "english_latin"
    elif dominant == "digit" and dominant_ratio > 0.6:
        label = "numbers"
    elif dominant == "punct" and dominant_ratio > 0.7:
        label = "punctuation"
    elif dominant == "symbol" and dominant_ratio > 0.6:
        label = "symbol_cluster"
    elif dominant == "control":
        label = "control_bytes"
    elif dominant in {
        "cjk", "hiragana", "katakana", "hangul", "thai", "greek", "variation_selector",
    }:
        label = _SCRIPT_LABELS[dominant]
    elif dominant == "whitespace" and printable < 0.4:
        label = "whitespace"
    elif dominant_ratio < 0.5 and printable_fraction < 0.7:
        label = "mixed_noise"

    if label not in {"punctuation", "symbol_cluster", "numbers"} and dominant not in {
        "latin", "cjk", "hiragana", "katakana", "hangul", "thai",
    }:
        dense = (char_counts.get("symbol", 0) + char_counts.get("punct", 0)) / total_chars
        if dense > 0.6 and total_chars >= 3:
            label = "gibberish_symbols"
    return label


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def _z_array(seq: Sequence[int]) -> List[int]:
    n = len(seq)
    z = [0] * n
    left = right = 0
    for i in range(1, n):
        if i < right:
            z[i] = min(right - i, z[i - left])
        while i + z[i] < n and seq[z[i]] == seq[i + z[i]]:
            z[i] += 1
        if i + z[i] > right:
            left, right = i, i + z[i]
    return z


def _exact_loop(tokens: Sequence[int], end_token_ids: Sequence[int]) -> bool:
    n = len(tokens)
    if n < _EL_MIN_LOOP_TOKENS:
        return False
    end_set = frozenset(end_token_ids)

    def _scan(seq: Sequence[int], min_loop: int) -> bool:
        z = _z_array(list(seq)[::-1])
        for period in range(1, len(seq) // 2 + 1):
            if (
                z[period] >= period
                and z[period] >= _EL_MIN_COPIES * period
                and period + z[period] >= min_loop
                and period >= _EL_MIN_PERIOD
            ):
                return True
        return False

    if _scan(tokens, _EL_MIN_LOOP_TOKENS):
        return True
    last_token = tokens[-1]
    stripped = list(tokens)
    for _ in range(_EL_STRIP_END):
        if not stripped or stripped[-1] not in end_set:
            break
        stripped.pop()
    if len(stripped) < _EL_MIN_LOOP_TOKENS:
        return False
    min_loop = _EL_NATURAL_END_MIN_LOOP if last_token == _QWEN3_IM_END else _EL_MIN_LOOP_TOKENS
    if min_loop < _EL_MIN_LOOP_TOKENS:
        min_loop = _EL_MIN_LOOP_TOKENS
    return _scan(stripped, min_loop)


def _block_count(tokens: Sequence[int], m: int, k: int) -> bool:
    if m <= 0 or k <= 1 or len(tokens) < m:
        return False
    counts: Dict[Tuple[int, ...], int] = {}
    for i in range(len(tokens) - m + 1):
        block = tuple(tokens[i : i + m])
        c = counts.get(block, 0) + 1
        if c >= k:
            return True
        counts[block] = c
    return False


def _packed_repeat(tokens: Sequence[int]) -> bool:
    W, m, k = _PR_WINDOW, _PR_BLOCK, _PR_MIN
    if W <= 0 or m <= 0 or k <= 1 or len(tokens) < W:
        return False
    positions: Dict[Tuple[int, ...], List[int]] = {}
    for i in range(len(tokens) - m + 1):
        positions.setdefault(tuple(tokens[i : i + m]), []).append(i)
    span = W - m
    for occ in positions.values():
        if len(occ) < k:
            continue
        for j in range(len(occ) - k + 1):
            if occ[j + k - 1] - occ[j] <= span:
                return True
    return False


def _foreign_splice(texts: Sequence[str], cats: Sequence[str]) -> bool:
    n = len(cats)
    for i in range(1, n - 1):
        if cats[i] not in _SPLICE_SCRIPTS:
            continue
        if not texts[i].strip():
            continue
        prev_cat, next_cat = cats[i - 1], cats[i + 1]
        if prev_cat in _WORD_CATS and (
            next_cat in _WORD_CATS
            or next_cat == "punctuation"
            or next_cat == "whitespace"
        ):
            return True
    return False


def _script_presence(cats: Sequence[str]) -> bool:
    return any(c == "thai" for c in cats)


def _foreign_island(cats: Sequence[str]) -> bool:
    n_foreign = max_run = run = 0
    for c in cats:
        if c in _SPLICE_SCRIPTS:
            n_foreign += 1
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return 0 < n_foreign <= _ISO_MAX_COUNT and max_run <= _ISO_MAX_RUN


def _fffd_embed(texts: Sequence[str], cats: Sequence[str]) -> bool:
    n = len(texts)
    for i in range(1, n - 1):
        if "\ufffd" not in texts[i]:
            continue
        if cats[i - 1] in _FFFD_CATS and cats[i + 1] in _FFFD_CATS:
            return True
    return False


def _feff_embed(texts: Sequence[str], cats: Sequence[str]) -> bool:
    n = len(texts)
    for i in range(1, n - 1):
        if "\ufeff" not in texts[i]:
            continue
        if cats[i - 1] == "english_latin" and cats[i + 1] == "english_latin":
            return True
    return False


def _hebrew_island(texts: Sequence[str]) -> bool:
    n_heb = 0
    for t in texts:
        if any("\u0590" <= ch <= "\u05FF" for ch in t):
            n_heb += 1
            if n_heb > 2:
                return False
    return n_heb >= 1


def _glyph_repeat(ids: Sequence[int], cats: Sequence[str]) -> bool:
    n_foreign = max_run = run = 0
    distinct = set()
    for t, c in zip(ids, cats):
        if c in _SPLICE_SCRIPTS:
            n_foreign += 1
            distinct.add(t)
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    if n_foreign == 0 or max_run > 1:
        return False
    n_distinct = len(distinct)
    if n_distinct == 1 and n_foreign >= _GR_MIN_REPEATS:
        return True
    return n_distinct <= _GR_MAX_DISTINCT and n_foreign >= _GR_MIN_COUNT


def _cjk_island(cats: Sequence[str]) -> bool:
    idxs = [i for i, c in enumerate(cats) if c == "chinese_cjk"]
    if not idxs or not (_CJK_MIN_COUNT <= len(idxs) <= _CJK_MAX_COUNT):
        return False
    for a, b in zip(idxs, idxs[1:]):
        if b - a <= _CJK_MAX_RUN:
            return False
    n = len(cats)
    for i in idxs:
        prev_cat = cats[i - 1] if i > 0 else None
        next_cat = cats[i + 1] if i + 1 < n else None
        if prev_cat in _CJK_WORD_CATS or next_cat in _CJK_WORD_CATS:
            return False
    return True


def _cjk_splice_single(cats: Sequence[str]) -> bool:
    idxs = [i for i, c in enumerate(cats) if c == "chinese_cjk"]
    if len(idxs) != 1:
        return False
    i = idxs[0]
    n = len(cats)
    prev_cat = cats[i - 1] if i > 0 else None
    next_cat = cats[i + 1] if i + 1 < n else None
    if prev_cat == "english_latin" or next_cat == "english_latin":
        return True
    if prev_cat == "numbers" or next_cat == "numbers":
        return True
    if prev_cat == "english_latin_space" and next_cat == "english_latin_space":
        return True
    if next_cat in ("english_latin_space", "whitespace") and prev_cat not in ("english_latin", "numbers"):
        return True
    return False


def _template_enum(ids: Sequence[int], texts: Sequence[str]) -> bool:
    n = len(ids)
    if n < 4:
        return False
    starts = []
    for i in range(n - 2):
        if texts[i].strip().isdigit() and texts[i + 1].strip() in _TE_SEPS:
            starts.append(i)
    m = len(starts)
    if m < _TE_MIN_ITEMS:
        return False
    ends = [starts[k + 1] if k + 1 < m else n for k in range(m)]
    bodies = [tuple(ids[starts[k] + 2 : ends[k]]) for k in range(m)]
    best = 1
    a = 0
    while a < m:
        if m - a <= best:
            break
        body = bodies[a]
        if len(body) < _TE_MIN_BODY:
            a += 1
            continue
        cnt = 1
        b = a + 1
        while b < m and bodies[b] == body:
            cnt += 1
            b += 1
        if cnt > best:
            best = cnt
        a = b
    return best >= _TE_MIN_ITEMS


def _sentence_repeat(text: str) -> bool:
    if not text:
        return False
    parts = re.split(r"(?<=[.!?])\s+|\n", text)
    positions: Dict[str, List[int]] = {}
    charpos = 0
    for p in parts:
        stripped = p.strip()
        if len(stripped.split()) >= _SR_MIN_WORDS:
            positions.setdefault(stripped, []).append(charpos)
        charpos += len(p) + 1
    total_best = packed_best = 0
    for occ in positions.values():
        occ.sort()
        total_best = max(total_best, len(occ))
        for j in range(len(occ)):
            for l in range(j + 1, len(occ)):
                if occ[l] - occ[j] <= _SR_WINDOW:
                    packed_best = max(packed_best, l - j + 1)
    return total_best >= _SR_MIN_TOTAL and packed_best >= _SR_MIN_PACKED


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def _prepare(
    token_ids: Sequence[int],
    token_texts: Optional[Sequence[str]],
    decode: Optional[Callable[[Sequence[int]], str]],
) -> Tuple[List[int], Optional[List[str]], Optional[List[str]], Optional[str]]:
    ids = [int(t) for t in token_ids]
    texts: Optional[List[str]] = None
    if token_texts is not None:
        texts = ["" if t is None else str(t) for t in token_texts]
        if len(texts) != len(ids):
            raise ValueError("token_texts length must match token_ids")
    elif decode is not None:
        texts = [(decode([t]) or "") for t in ids]

    cats = [categorize_text(t) for t in texts] if texts is not None else None

    full: Optional[str] = None
    if decode is not None:
        full = decode(ids) or ""
    elif texts is not None:
        full = "".join(texts)
    return ids, texts, cats, full


def detect(
    token_ids: Sequence[int],
    *,
    token_texts: Optional[Sequence[str]] = None,
    decode: Optional[Callable[[Sequence[int]], str]] = None,
    end_token_ids: Sequence[int] = DEFAULT_END_TOKEN_IDS,
) -> DetectResult:
    """Score one generated token trajectory.

    ``token_texts[i]`` is the decoded string of ``token_ids[i]``.
    ``decode(ids)`` turns a span of ids into text. Either is enough to
    enable script / sentence rules; with neither, only token-id rules run.
    """
    if not token_ids:
        return DetectResult(False, 0, "")

    ids, texts, cats, full = _prepare(token_ids, token_texts, decode)

    if texts is not None and cats is not None:
        if _foreign_splice(texts, cats):
            return DetectResult(True, 1, "foreign_splice")
        if _script_presence(cats):
            return DetectResult(True, 1, "script_presence")
        if _foreign_island(cats):
            return DetectResult(True, 1, "foreign_island")
        if _fffd_embed(texts, cats):
            return DetectResult(True, 1, "fffd_embed")
        if _feff_embed(texts, cats):
            return DetectResult(True, 1, "feff_embed")
        if _hebrew_island(texts):
            return DetectResult(True, 1, "hebrew_island")
        if _glyph_repeat(ids, cats):
            return DetectResult(True, 1, "glyph_repeat")
        if _cjk_island(cats):
            return DetectResult(True, 1, "cjk_island")
        if _cjk_splice_single(cats):
            return DetectResult(True, 1, "cjk_splice_single")

    if _exact_loop(ids, end_token_ids):
        return DetectResult(True, 3, "exact_loop")
    if _block_count(ids, _BR_BLOCK, _BR_MIN):
        return DetectResult(True, 3, "block_repeat")
    if _packed_repeat(ids):
        return DetectResult(True, 3, "packed_repeat")
    if _block_count(ids, _BLR_BLOCK, _BLR_MIN):
        return DetectResult(True, 3, "block_repeat_large")

    if texts is not None:
        if _template_enum(ids, texts):
            return DetectResult(True, 3, "template_enum")
    if full is not None:
        if _sentence_repeat(full):
            return DetectResult(True, 3, "sentence_repeat")

    return DetectResult(False, 0, "")
