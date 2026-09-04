"""Fakten-Gedächtnis nach dem Mem0-Muster: ADD-only (nichts überschreiben),
zeitliche Gültigkeit (valid_from/valid_to, supersede) und Decay-Ranking im Retrieval."""
from typing import Any

_HALF_LIFE_SEC = 14 * 86400 / 0.693147  # 14-Tage-Halbwertszeit für Relevanz


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def record_fact(store, subject: str, predicate: str, obj: str,
                confidence: float = 0.7, source: str = "learned",
                space: str = "default") -> int:
    """ADD-only: identischer Fakt → touch; Widerspruch → neuer Fakt, alter verliert Gültigkeit."""
    store._ensure_dim()
    emb = store.embed.embed(f"{subject} {predicate} {obj}")
    with store.connect() as conn:
        row = conn.execute(
            "select id, object from facts where space=%s and subject=%s and predicate=%s "
            "and valid_to is null order by created_at desc limit 1",
            (space, _norm(subject), _norm(predicate)),
        ).fetchone()
        if row and _norm(row[1]) == _norm(obj):
            conn.execute(
                "update facts set last_seen = now(), confidence = greatest(confidence, %s) where id = %s",
                (confidence, row[0]),
            )
            conn.commit()
            return row[0]
        fid = conn.execute(
            "insert into facts (subject, predicate, object, confidence, source, embedding, space) "
            "values (%s,%s,%s,%s,%s,%s,%s) returning id",
            (_norm(subject), _norm(predicate), str(obj), confidence, source, emb, space),
        ).fetchone()[0]
        if row:
            conn.execute("update facts set valid_to = now(), superseded_by = %s where id = %s", (fid, row[0]))
        conn.commit()
    if row:
        try:
            store.add_edge(f"fakten:{row[0]}", f"fakten:{fid}", "supersede", space=space)
        except Exception:
            pass
    return fid


def active_facts(store, limit: int = 20, space: str = "default") -> list[dict[str, Any]]:
    """Nur aktuell gültige Fakten (valid_to is null)."""
    with store.connect() as conn:
        rows = conn.execute(
            "select id, subject, predicate, object, confidence, source, created_at from facts "
            "where space = %s and valid_to is null order by created_at desc limit %s",
            (space, limit),
        ).fetchall()
    return [dict(zip(("id", "subject", "predicate", "object", "confidence", "source", "at"), r)) for r in rows]


def recall_facts(store, text: str, limit: int = 5, space: str = "default") -> list[dict[str, Any]]:
    """Semantische Suche, gewichtet mit Zeit-Decay: ähnlich UND aktuell gewinnt."""
    store._ensure_dim()
    vec = store._vector_literal(store.embed.embed(text))
    with store.connect() as conn:
        rows = conn.execute(
            "select id, subject, predicate, object, confidence, source, "
            "(1-(embedding <=> %s::vector)) * exp(-extract(epoch from (now()-last_seen))/%s) rank, "
            "1-(embedding <=> %s::vector) sim "
            "from facts where space = %s and valid_to is null "
            "order by rank desc limit %s",
            (vec, _HALF_LIFE_SEC, vec, space, limit),
        ).fetchall()
    return [{"id": r[0], "subject": r[1], "predicate": r[2], "object": r[3],
             "confidence": float(r[4]), "source": r[5], "score": float(r[6])} for r in rows]
