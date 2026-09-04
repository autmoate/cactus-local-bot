from hashlib import blake2b
from math import sqrt

import requests

HASH_DIM = 64


def _hash_vec(text: str) -> list[float]:
    vec = [0.0] * HASH_DIM
    for token in text.lower().split():
        digest = blake2b(token.encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % HASH_DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _fallback_vec(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for token in text.lower().split():
        digest = blake2b(token.encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


class EmbeddingClient:
    def __init__(self, base_url: str, model: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._dim: int | None = None

    def _resolve_model(self) -> str | None:
        if self.model:
            return self.model
        try:
            response = requests.get(f"{self.base_url}/models", timeout=5)
            response.raise_for_status()
            models = (response.json().get("data") or [])
            return models[0]["id"] if models else None
        except requests.RequestException:
            return None

    def dimension(self) -> int | None:
        return self._dim

    def online(self) -> bool:
        return self._resolve_model() is not None

    def embed(self, text: str) -> list[float]:
        model = self._resolve_model()
        if model is None:
            return _fallback_vec(text, self._dim or HASH_DIM)
        try:
            response = requests.post(
                f"{self.base_url}/embeddings",
                json={"model": model, "input": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            vector = response.json()["data"][0]["embedding"]
            if self._dim is not None and len(vector) != self._dim:
                return _fallback_vec(text, self._dim)
            self._dim = len(vector)
            return vector
        except Exception:
            return _fallback_vec(text, self._dim or HASH_DIM)
