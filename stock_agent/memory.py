"""Optional Qdrant evidence index with swappable, local embeddings."""
from datetime import datetime, timezone
from hashlib import sha256
from math import sqrt
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import UUID, uuid5, NAMESPACE_URL
from .models import DataError

class HashEmbedding:
    """Offline lexical baseline for tests; replace with a semantic model for real research."""
    name = "hash-lexical-v1"
    dimensions = 128

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1 if digest[4] % 2 else -1
        magnitude = sqrt(sum(value * value for value in vector))
        return [value / magnitude for value in vector] if magnitude else vector

class SentenceTransformerEmbedding:
    """Optional local semantic provider: pip install sentence-transformers."""
    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise DataError("sentence-transformers is not installed") from exc
        self.model = SentenceTransformer(model_name)
        self.name = model_name
        self.dimensions = len(self.embed("dimension probe"))

    def embed(self, text: str) -> list[float]:
        return self.model.encode(text, normalize_embeddings=True).tolist()

def embedding(provider: str, model_name: str | None = None):
    if provider == "hash":
        return HashEmbedding()
    if provider == "sentence-transformers":
        return SentenceTransformerEmbedding(model_name or "all-MiniLM-L6-v2")
    raise DataError(f"unknown embedding provider {provider!r}")

class QdrantMemory:
    def __init__(self, url: str, collection: str, embedder):
        self.url = url.rstrip("/")
        self.collection = collection
        self.embedder = embedder

    def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        body = json.dumps(data).encode() if data is not None else None
        request = Request(self.url + path, data=body, method=method,
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise DataError(f"Qdrant HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise DataError(f"Qdrant unavailable at {self.url}: {exc.reason}") from exc

    def ensure_collection(self) -> None:
        path = "/collections/" + quote(self.collection, safe="")
        try:
            info = self._request("GET", path)
            size = info["result"]["config"]["params"]["vectors"]["size"]
            if size != self.embedder.dimensions:
                raise DataError(f"Qdrant collection vector size {size} differs from embedding size {self.embedder.dimensions}")
        except DataError as exc:
            if "HTTP 404" not in str(exc):
                raise
            self._request("PUT", path, {"vectors": {"size": self.embedder.dimensions, "distance": "Cosine"}})
        # Date indexes give Qdrant the correct type for range filters.
        for field in ("published_at", "available_at"):
            self._request("PUT", path + "/index?wait=true", {"field_name": field, "field_schema": "datetime"})

    def index_articles(self, db) -> int:
        self.ensure_collection()
        rows = db.execute("SELECT * FROM articles ORDER BY article_id").fetchall()
        points = []
        for row in rows:
            tickers = [r[0] for r in db.execute("SELECT ticker FROM article_tickers WHERE article_id=?", (row["article_id"],))]
            payload = {key: row[key] for key in ("article_id", "title", "source", "url", "published_at",
                                                    "retrieved_at", "available_at", "content_hash", "canonical_key")}
            payload["tickers"] = tickers
            payload["embedding_model"] = self.embedder.name
            points.append({"id": str(uuid5(NAMESPACE_URL, row["article_id"])),
                           "vector": self.embedder.embed(row["title"] + " " + row["content"]),
                           "payload": payload})
        for start in range(0, len(points), 100):
            self._request("PUT", f"/collections/{quote(self.collection, safe='')}/points?wait=true",
                          {"points": points[start:start + 100]})
        return len(points)

    def search_memory(self, query: str, tickers: list[str] | None = None,
                      published_before: datetime | None = None, published_after: datetime | None = None,
                      limit: int = 10, available_before: datetime | None = None) -> list[dict]:
        if limit < 1 or not query.strip():
            raise DataError("memory query must be nonempty and limit positive")
        # Availability is always bounded, including when only a publication cutoff was supplied.
        availability = available_before or published_before or datetime.now(timezone.utc)
        if availability.tzinfo is None:
            raise DataError("memory cutoff must have timezone")
        must = [{"key": "available_at", "range": {"lte": availability.astimezone(timezone.utc).isoformat()}}]
        if published_before:
            must.append({"key": "published_at", "range": {"lte": published_before.astimezone(timezone.utc).isoformat()}})
        if published_after:
            must.append({"key": "published_at", "range": {"gte": published_after.astimezone(timezone.utc).isoformat()}})
        if tickers:
            must.append({"should": [{"key": "tickers", "match": {"value": ticker}} for ticker in tickers]})
        result = self._request("POST", f"/collections/{quote(self.collection, safe='')}/points/query",
                               {"query": self.embedder.embed(query), "filter": {"must": must},
                                "limit": limit, "with_payload": True})
        return [{"score": point["score"], **point["payload"]} for point in result["result"]["points"]]
