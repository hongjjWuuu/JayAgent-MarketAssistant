from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from app.core.config import settings


class EmbeddingProvider(Protocol):
    model_id: str
    dimensions: int

    def embed_query(self, text: str) -> list[float]: ...
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    """Deterministic offline embedding used by the pilot; replaceable by a real provider."""

    dimensions = 1536
    model_id = "hash-embedding-v1"

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += -1.0 if digest[4] & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model_id: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        self.base_url = base_url if base_url is not None else settings.embedding_base_url
        self.api_key = api_key if api_key is not None else settings.embedding_api_key
        if not self.base_url:
            raise RuntimeError("Embedding base URL is required")
        self.model_id = model_id or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimension

    def _embed(self, values: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(
            f"{self.base_url.rstrip('/')}/embeddings",
            headers=headers,
            json={"model": self.model_id, "input": values},
            timeout=30,
        )
        response.raise_for_status()
        vectors = [item["embedding"] for item in response.json()["data"]]
        if any(len(vector) != self.dimensions for vector in vectors):
            raise ValueError("Embedding dimension does not match configured dimension")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)


def get_embedding_provider() -> EmbeddingProvider:
    if settings.embedding_provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider()
    return HashEmbeddingProvider()


def embedding_provider_for(
    provider: str, model_id: str, dimensions: int
) -> EmbeddingProvider:
    if provider == "hash":
        value = HashEmbeddingProvider()
        if model_id != value.model_id or dimensions != value.dimensions:
            raise ValueError("Hash embedding profile does not match hash-embedding-v1/1536")
        return value
    if provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            model_id=model_id,
            dimensions=dimensions,
        )
    raise ValueError(f"Unsupported embedding provider: {provider}")
