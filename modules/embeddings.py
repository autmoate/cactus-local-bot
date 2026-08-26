from hashlib import blake2b
from math import sqrt

DIM = 64


def embed_text(text: str) -> list[float]:
    vec = [0.0] * DIM
    for token in text.lower().split():
        digest = blake2b(token.encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]
