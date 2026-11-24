"""Deterministic, dependency-free vector index.

This is the project's vector store choice for local/demo deployments: a
hashed bag-of-words + bigram embedding with cosine similarity.  It needs no
API key, is stable across processes, and keeps candidate text on-device.

The interface is intentionally small (``add``/``search``) so it can be swapped
for pgvector, Chroma, Pinecone or Firestore vector search in production
without touching callers.  Persisted version facts remain the source of truth;
the index is rebuilt in memory from verified facts only.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass, field

DEFAULT_DIMENSIONS = 384
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#./-]*")


def tokenize(text: str) -> list[str]:
    words = TOKEN_RE.findall((text or "").lower())
    bigrams = [f"{first}_{second}" for first, second in zip(words, words[1:])]
    return words + bigrams


def embed(text: str, dimensions: int = DEFAULT_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    counts = Counter(tokenize(text))
    if not counts:
        return vector
    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % dimensions
        sign = 1.0 if (value >> 63) & 1 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(component * component for component in vector))
    if norm:
        vector = [component / norm for component in vector]
    return vector


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


@dataclass
class VectorRecord:
    record_id: str
    text: str
    vector: list[float] = field(repr=False)
    metadata: dict = field(default_factory=dict)


class VectorIndex:
    """In-memory vector index with exact cosine search."""

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.records: list[VectorRecord] = []

    def add(self, record_id: str, text: str, metadata: dict | None = None) -> None:
        if not (text or "").strip():
            return
        self.records.append(
            VectorRecord(
                record_id=record_id,
                text=text,
                vector=embed(text, self.dimensions),
                metadata=metadata or {},
            )
        )

    def extend(self, items: list[tuple[str, str, dict | None]]) -> None:
        for record_id, text, metadata in items:
            self.add(record_id, text, metadata)

    def search(self, query: str, top_k: int = 3, min_score: float = 0.0) -> list[tuple[float, VectorRecord]]:
        query_vector = embed(query, self.dimensions)
        scored = [
            (cosine(query_vector, record.vector), record)
            for record in self.records
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for item in scored[:top_k] if item[0] >= min_score]

    def __len__(self) -> int:
        return len(self.records)
