"""Hand-rolled TF-IDF retrieval.

No numpy, no external vector store. At corpus scale (a few thousand short
documents) an exact sparse cosine is both fast enough and fully inspectable,
which matters more here than raw speed: every retrieved precedent has to be
explainable to a merchant or an acquirer.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Iterable

_TOKEN = re.compile(r"[a-z0-9]+")

_STOP = {
    "a", "an", "and", "the", "for", "with", "of", "to", "in", "on", "at", "by",
    "is", "are", "be", "it", "that", "this", "as", "or", "from", "your", "you",
    "we", "our", "us", "their", "its", "into", "per", "via", "then", "than",
}


def stem(token: str) -> str:
    """A deliberately conservative suffix stripper.

    Without it "libraries" never matches "library" and "ebooks" never matches
    "ebook", which silently breaks precedent retrieval and pattern matching in a
    way that looks like the memory simply holding nothing. Full Porter stemming
    would be more aggressive than this corpus needs and harder to audit -- these
    four rules cover the plural and participle forms that actually occur in
    merchant pitches.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed") and not token.endswith("eed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall((text or "").lower())
            if len(t) > 1 and t not in _STOP]


class TfIdf:
    """A tiny exact TF-IDF index over short documents."""

    def __init__(self) -> None:
        self.df: Counter[str] = Counter()
        self.docs: dict[str, dict[str, float]] = {}
        self.raw: dict[str, list[str]] = {}
        self._n = 0
        self._dirty = True

    def prime(self, texts: Iterable[str]) -> "TfIdf":
        """Fit document frequencies over a background corpus.

        Without this, a store holding two documents produces nonsense IDF --
        terms absent from the index score *higher* than shared ones, so two
        near-identical statements can look unrelated. Priming over the merchant
        corpus gives every term a stable, meaningful weight from the first write.
        """
        for i, t in enumerate(texts):
            self.raw[f"__prime{i}__"] = tokenize(t)
        self._dirty = True
        return self

    def add(self, doc_id: str, text: str) -> None:
        toks = tokenize(text)
        self.raw[doc_id] = toks
        self._dirty = True

    def build(self) -> "TfIdf":
        self.df.clear()
        for toks in self.raw.values():
            for t in set(toks):
                self.df[t] += 1
        self._n = max(1, len(self.raw))
        self.docs = {d: self._vector(toks) for d, toks in self.raw.items()}
        self._dirty = False
        return self

    def _idf(self, term: str) -> float:
        return math.log((self._n + 1) / (self.df.get(term, 0) + 1)) + 1.0

    def _vector(self, toks: list[str]) -> dict[str, float]:
        if not toks:
            return {}
        tf = Counter(toks)
        n = len(toks)
        vec = {t: (c / n) * self._idf(t) for t, c in tf.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def vector(self, text: str) -> dict[str, float]:
        if self._dirty:
            self.build()
        return self._vector(tokenize(text))

    @staticmethod
    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(t, 0.0) for t, v in a.items())

    def search(
        self,
        text: str,
        limit: int = 10,
        exclude: Iterable[str] = (),
        candidates: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        if self._dirty:
            self.build()
        q = self.vector(text)
        if not q:
            return []
        skip = set(exclude)
        pool = self.docs.items() if candidates is None else (
            (d, self.docs[d]) for d in candidates if d in self.docs
        )
        scored = [(d, self.cosine(q, v)) for d, v in pool
                  if d not in skip and not d.startswith("__prime")]
        scored = [(d, s) for d, s in scored if s > 0.0]
        scored.sort(key=lambda kv: -kv[1])
        return [(d, round(s, 4)) for d, s in scored[:limit]]

    def overlap_terms(self, a: str, b: str, limit: int = 6) -> list[str]:
        """The shared terms that drove a match -- shown in the brief so a
        reviewer can see *why* two cases were considered similar."""
        va, vb = self.vector(a), self.vector(b)
        shared = [(t, va[t] * vb.get(t, 0.0)) for t in va if t in vb]
        shared.sort(key=lambda kv: -kv[1])
        return [t for t, w in shared[:limit] if w > 0]
