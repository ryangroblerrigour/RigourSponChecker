"""Brand matching for spontaneous awareness open ends.

Takes one free text answer (commas, new lines or run on text) and returns
one result per brand mention, plus one NULL result per piece of text that
did not match anything in the code frame.

Rules
- Exact matches on known aliases are always preferred.
- Spelling tolerance is deliberately strict:
    words of 3 letters or fewer must match exactly (DPD, UPS, DX, APC)
    words of 4 or 5 letters may have 1 typo
    words of 6 letters or more may have 2 typos
    the first letter must always match
- If a typo could fit two different codes equally well, it returns NULL.
- "DHL" on its own is ambiguous (E-commerce or Express) so returns NULL.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz.distance import OSA

CODEFRAME_DIR = Path(__file__).parent / "codeframes"
MAX_WINDOW = 4  # longest phrase (in words) we try to match

# Words that split answers in a run on sentence ("dpd and evri")
SPLIT_WORDS = {"and", "or", "also", "plus", "then", "n"}
# Words dropped from the edges of unmatched text so they do not become NULL rows
FILLER_WORDS = {
    "the", "a", "an", "i", "think", "maybe", "probably", "possibly", "etc",
    "with", "of", "too", "as", "well", "some", "like", "um", "erm",
}

TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*|[,;/|+&.\n\r!?:]", re.UNICODE)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['’]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def allowed_typos(word: str) -> int:
    n = len(word)
    if n <= 3:
        return 0
    if n <= 5:
        return 1
    return 2


@dataclass
class Alias:
    words: tuple[str, ...]
    compact: str
    codes: tuple[int, ...]  # one code, or several when ambiguous


@dataclass
class Codeframe:
    name: str
    labels: dict[int, str]
    aliases: list[Alias] = field(default_factory=list)
    exact: dict[str, Alias] = field(default_factory=dict)

    @classmethod
    def load(cls, name: str) -> "Codeframe":
        path = CODEFRAME_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        cf = cls(name=data["name"], labels={c["code"]: c["label"] for c in data["codes"]})
        entries = [(a, (c["code"],)) for c in data["codes"] for a in [c["label"], *c["aliases"]]]
        entries += [(a["alias"], tuple(a["codes"])) for a in data.get("ambiguous", [])]
        seen: set[tuple[str, ...]] = set()
        for raw, codes in entries:
            norm = normalise(raw)
            if not norm:
                continue
            alias = Alias(words=tuple(norm.split()), compact=norm.replace(" ", ""), codes=codes)
            existing = cf.exact.get(alias.compact)
            if existing and existing.codes != codes:
                merged = tuple(sorted(set(existing.codes) | set(codes)))
                alias = Alias(alias.words, alias.compact, merged)
            cf.exact[alias.compact] = alias
            # keep both "royal mail" and "royalmail" shapes for typo matching
            if alias.words not in seen:
                seen.add(alias.words)
                cf.aliases.append(alias)
        return cf


@dataclass
class Token:
    text: str      # normalised word
    start: int
    end: int


@dataclass
class Result:
    verbatim: str
    code: int | None
    label: str | None
    match: str            # exact | fuzzy | ambiguous | none
    candidates: list[int] | None = None

    def as_dict(self) -> dict:
        d = {"verbatim": self.verbatim, "code": self.code, "label": self.label, "match": self.match}
        if self.candidates:
            d["candidates"] = self.candidates
        return d


def _segments(raw: str) -> list[list[Token]]:
    """Split text into runs of words, breaking on punctuation and joining words."""
    segments: list[list[Token]] = [[]]
    for m in TOKEN_RE.finditer(raw):
        piece = m.group(0)
        norm = normalise(piece)
        if not norm or norm in SPLIT_WORDS:
            segments.append([])
            continue
        segments[-1].append(Token(norm.replace(" ", ""), m.start(), m.end()))
    return [s for s in segments if s]


def _fuzzy_word(a: str, b: str) -> int | None:
    """Typo distance between two words, or None if too far apart."""
    if a == b:
        return 0
    if a[0] != b[0]:
        return None
    limit = allowed_typos(b)
    if limit == 0:
        return None
    d = OSA.distance(a, b, score_cutoff=limit)
    return d if d <= limit else None


def _match_window(words: list[str], cf: Codeframe) -> tuple[Alias, str] | None:
    compact = "".join(words)
    hit = cf.exact.get(compact)
    if hit:
        return hit, "exact"

    best: list[tuple[int, Alias]] = []
    for alias in cf.aliases:
        dist = None
        if len(alias.words) == len(words):
            total = 0
            for w, aw in zip(words, alias.words):
                d = _fuzzy_word(w, aw)
                if d is None:
                    total = None
                    break
                total += d
            dist = total
        if dist is None and len(words) == 1 and len(alias.words) > 1:
            dist = _fuzzy_word(compact, alias.compact)  # e.g. "royalmial"
        if dist is not None:
            best.append((dist, alias))
    if not best:
        return None
    best.sort(key=lambda x: x[0])
    top = best[0][0]
    codes = {c for d, a in best if d == top for c in a.codes}
    if len(codes) > 1:
        return Alias(words=tuple(words), compact=compact, codes=tuple(sorted(codes))), "fuzzy"
    return best[0][1], "fuzzy"


def _unknown(raw: str, tokens: list[Token]) -> Result | None:
    while tokens and (tokens[0].text in FILLER_WORDS or tokens[0].text.isdigit()):
        tokens = tokens[1:]
    while tokens and (tokens[-1].text in FILLER_WORDS or tokens[-1].text.isdigit()):
        tokens = tokens[:-1]
    if not tokens:
        return None
    return Result(raw[tokens[0].start:tokens[-1].end], None, None, "none")


def code_text(raw: str, cf: Codeframe) -> list[Result]:
    results: list[Result] = []
    for seg in _segments(raw):
        pending: list[Token] = []
        i = 0
        while i < len(seg):
            found = None
            for size in range(min(MAX_WINDOW, len(seg) - i), 0, -1):
                window = seg[i:i + size]
                m = _match_window([t.text for t in window], cf)
                if m:
                    found = (size, window, *m)
                    break
            if not found:
                pending.append(seg[i])
                i += 1
                continue
            if pending:
                u = _unknown(raw, pending)
                if u:
                    results.append(u)
                pending = []
            size, window, alias, how = found
            verbatim = raw[window[0].start:window[-1].end]
            if len(alias.codes) == 1:
                code = alias.codes[0]
                results.append(Result(verbatim, code, cf.labels[code], how))
            else:
                results.append(Result(verbatim, None, None, "ambiguous", list(alias.codes)))
            i += size
        if pending:
            u = _unknown(raw, pending)
            if u:
                results.append(u)
    return results


_cache: dict[str, Codeframe] = {}


def get_codeframe(name: str) -> Codeframe:
    if name not in _cache:
        _cache[name] = Codeframe.load(name)
    return _cache[name]


def available_codeframes() -> list[str]:
    return sorted(p.stem for p in CODEFRAME_DIR.glob("*.json"))
