"""A fallback-only semantic index over rules.

This is deliberately **not** a source of truth: every document it holds is a copy of a
rule that already lives in the ``MongoRulesetRepository``, written there first. Its only
two jobs are (1) `/v1/rules/search`-style semantic browsing, and (2) the decision
service's vector-search fallback when the JSON engine finds no matching rule anywhere.

Two interchangeable backends behind one interface (``VectorRuleIndex`` in
``interfaces.py``):

    * **MongoDB Atlas** (default when reachable) — each rule is one document holding the
      full rule JSON, its metadata, and an OpenAI embedding; semantic search runs through
      Atlas Vector Search (``langchain-mongodb`` + ``OpenAIEmbeddings``).
    * **Memory** — a dependency-free dict + TF-IDF index, optionally persisted to a JSON
      file. Keeps the app (and the hermetic test suite) working with no network or key.
"""
import copy
import json
import math
import os
import re
from collections import Counter
from typing import Any, Optional

from ..logging_config import get_logger

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_EMBEDDING_DIMENSIONS = 1536  # OpenAIEmbeddings default (text-embedding-3-small / ada-002)


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _collect_facts(node: dict, out: list[str]) -> None:
    if "fact" in node:
        out.append(node["fact"])
        return
    for combinator in ("all", "any", "none"):
        for child in node.get(combinator, []):
            _collect_facts(child, out)


def _document_text(ruleset_id: str, decision_type: str, rule: dict) -> str:
    """The searchable blob for a rule — what embeddings/TF-IDF rank over."""
    then = rule.get("then", {})
    facts: list[str] = []
    _collect_facts(rule.get("when", {}), facts)
    parts = [
        decision_type,
        ruleset_id,
        rule.get("phase", ""),
        then.get("verdict", ""),
        " ".join(f.replace(".", " ").replace("_", " ") for f in facts),
        " ".join(c.replace("_", " ").lower() for c in then.get("reason_codes", [])),
    ]
    return " ".join(p for p in parts if p)


def _clean_rule(rule: dict) -> dict:
    """A JSON-safe copy of a rule with any compiled-scratch key stripped."""
    r = copy.deepcopy(rule)
    r.pop("_compiled", None)
    if isinstance(r.get("when"), dict):
        _strip_compiled(r["when"])
    return r


def _strip_compiled(node: dict) -> None:
    node.pop("_compiled", None)
    for combinator in ("all", "any", "none"):
        for child in node.get(combinator, []):
            _strip_compiled(child)


def _search_result(ruleset_id: str, rule: dict, status: str, score: Optional[float]) -> dict:
    then = rule.get("then", {})
    facts: list[str] = []
    _collect_facts(rule.get("when", {}), facts)
    return {
        "ruleset_id": ruleset_id,
        "rule_id": rule.get("id"),
        "phase": rule.get("phase"),
        "verdict": then.get("verdict"),
        "reason_codes": then.get("reason_codes", []),
        "facts": facts,
        "status": status,
        "score": score,
    }


def _doc_id(ruleset_id: str, rule_id: str) -> str:
    return f"{ruleset_id}::{rule_id}"


# ===========================================================================
# Backends
# ===========================================================================
class InMemoryVectorIndex:
    """Dependency-free TF-IDF index, optionally persisted to a JSON file."""

    kind = "memory"

    def __init__(self, persist_path: str = "") -> None:
        self._persist_path = persist_path
        # {doc_id: {"ruleset_id", "decision_type", "rule", "status", "ruleset_version"}}
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._persist_path and os.path.exists(self._persist_path):
            with open(self._persist_path, "r", encoding="utf-8") as fh:
                self._data = json.load(fh)

    def _save(self) -> None:
        if not self._persist_path:
            return
        os.makedirs(os.path.dirname(self._persist_path) or ".", exist_ok=True)
        with open(self._persist_path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh)

    def put_rule(
        self,
        ruleset_id: str,
        rule: dict,
        decision_type: str,
        ruleset_version: int,
        status: str = "ACTIVE",
    ) -> None:
        clean = _clean_rule(rule)
        self._data[_doc_id(ruleset_id, clean["id"])] = {
            "ruleset_id": ruleset_id,
            "decision_type": decision_type,
            "rule": clean,
            "status": status,
            "ruleset_version": ruleset_version,
        }
        self._save()

    def delete_rule(self, ruleset_id: str, rule_id: str) -> bool:
        existed = self._data.pop(_doc_id(ruleset_id, rule_id), None) is not None
        if existed:
            self._save()
        return existed

    def rule_count(self) -> int:
        return len(self._data)

    def search(self, query: str, k: int) -> list[dict]:
        """Rank documents by TF-IDF cosine similarity (score in [0, 1]).

        Previously this normalized by dividing every score by the single best score in
        the result set, which made the top hit *always* exactly 1.0 regardless of how
        weakly it actually matched the query — silently defeating
        ``VECTOR_SEARCH_THRESHOLD`` (nothing could ever be rejected as "no match"). True
        cosine similarity (dot product over both vectors' norms) fixes that: a score
        reflects absolute closeness to a document, not relative rank among candidates.
        """
        corpus = list(self._data.values())
        if not corpus:
            return []

        tokenized = [
            (entry, _tokenize(_document_text(entry["ruleset_id"], entry["decision_type"], entry["rule"])))
            for entry in corpus
        ]

        df: Counter = Counter()
        for _, tokens in tokenized:
            df.update(set(tokens))
        n = len(tokenized)
        idf = {t: math.log(1 + n / (1 + c)) for t, c in df.items()}

        q_counts = Counter(_tokenize(query))
        if not q_counts:
            return []
        q_weights = {t: c * idf.get(t, 0.0) for t, c in q_counts.items()}
        q_norm = math.sqrt(sum(w * w for w in q_weights.values()))
        if q_norm == 0:
            return []

        scored: list[tuple[float, dict]] = []
        for entry, tokens in tokenized:
            counts = Counter(tokens)
            d_weights = {t: c * idf.get(t, 0.0) for t, c in counts.items()}
            d_norm = math.sqrt(sum(w * w for w in d_weights.values()))
            if d_norm == 0:
                continue
            dot = sum(w * d_weights.get(t, 0.0) for t, w in q_weights.items())
            score = dot / (q_norm * d_norm)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:k]
        return [
            _search_result(entry["ruleset_id"], entry["rule"], entry["status"], round(s, 4))
            for s, entry in top
        ]


class MongoAtlasVectorIndex:
    """MongoDB Atlas Vector Search-backed index. Each rule is one document carrying the
    full rule JSON, its metadata, the searchable ``text`` blob and an OpenAI
    ``embedding``; semantic search runs through Atlas Vector Search via
    ``langchain-mongodb``.
    """

    kind = "mongodb"

    def __init__(self, collection, embeddings, index_name: str) -> None:
        self._col = collection
        self._embeddings = embeddings
        # The vector store wrapper is used purely for querying; writes go through pymongo
        # so we own the document shape (full rule JSON + metadata). auto_create_index makes
        # the Atlas vector index on first use if it is missing.
        from langchain_mongodb import MongoDBAtlasVectorSearch

        self._vs = MongoDBAtlasVectorSearch(
            collection=collection,
            embedding=embeddings,
            index_name=index_name,
            text_key="text",
            embedding_key="embedding",
            dimensions=_EMBEDDING_DIMENSIONS,
            auto_create_index=True,
        )

    def put_rule(
        self,
        ruleset_id: str,
        rule: dict,
        decision_type: str,
        ruleset_version: int,
        status: str = "ACTIVE",
    ) -> None:
        # An upsert by document id, so put_rule doubles as both add and update. The
        # embedding is computed from the same searchable blob the memory backend ranks over.
        clean = _clean_rule(rule)
        text = _document_text(ruleset_id, decision_type, clean)
        embedding = self._embeddings.embed_query(text)
        self._col.update_one(
            {"_id": _doc_id(ruleset_id, clean["id"])},
            {"$set": {
                "ruleset_id": ruleset_id,
                "decision_type": decision_type,
                "rule_id": clean["id"],
                "phase": clean.get("phase", ""),
                "status": status,
                "ruleset_version": ruleset_version,
                "rule": clean,
                "text": text,
                "embedding": embedding,
            }},
            upsert=True,
        )

    def delete_rule(self, ruleset_id: str, rule_id: str) -> bool:
        res = self._col.delete_one({"_id": _doc_id(ruleset_id, rule_id)})
        return res.deleted_count > 0

    def rule_count(self) -> int:
        return self._col.count_documents({})

    def search(self, query: str, k: int) -> list[dict]:
        if self.rule_count() == 0:
            return []
        # A search failure (e.g. the vector index is still building) is non-fatal — the
        # decision service and /v1/rules/search both treat an empty result as "no match".
        try:
            hits = self._vs.similarity_search_with_score(query, k=k)
        except Exception as exc:
            logger.warning(
                "vectorindex.search_failed",
                extra={"event": "vectorindex.search_failed", "error": str(exc)},
            )
            return []
        out: list[dict] = []
        for doc, score in hits:
            meta = doc.metadata
            rule = meta.get("rule")
            if isinstance(rule, str):  # defensive: tolerate a JSON-string rule payload
                rule = json.loads(rule)
            out.append(
                _search_result(meta.get("ruleset_id"), rule, meta.get("status", "ACTIVE"), round(float(score), 4))
            )
        return out


def build_vector_index(settings: Any):
    mode = getattr(settings, "vector_backend", "auto")
    if mode != "memory":
        try:
            from pymongo import MongoClient  # pymongo is a hard dep (auth)
            from langchain_openai import OpenAIEmbeddings  # lazy: optional dependency

            api_key = getattr(settings, "openai_api_key", "").strip()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for Atlas Vector Search embeddings")

            client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
            client.admin.command("ping")  # fail fast to the memory fallback if unreachable
            collection = client[settings.mongodb_database][settings.mongodb_vector_collection]
            embeddings = OpenAIEmbeddings(api_key=api_key)
            backend = MongoAtlasVectorIndex(collection, embeddings, settings.mongodb_vector_index)
            logger.info("vectorindex.backend", extra={"event": "vectorindex.backend", "backend": "mongodb"})
            return backend
        except Exception as exc:
            if mode == "mongodb":
                raise
            logger.info(
                "vectorindex.mongodb_unavailable_memory_fallback",
                extra={"event": "vectorindex.mongodb_unavailable", "error": str(exc)},
            )
    return InMemoryVectorIndex(getattr(settings, "rules_store_path", ""))
