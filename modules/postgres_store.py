import json
from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from modules.embeddings import DIM, embed_text


class PostgresStore:
    def __init__(self, url: str):
        self.url = url

    def connect(self):
        conn = psycopg.connect(self.url)
        register_vector(conn)
        return conn

    def ping(self) -> bool:
        try:
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("select 1")
                return cur.fetchone()[0] == 1
        except Exception:
            return False

    def init(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("create extension if not exists vector")
        with self.connect() as conn:
            conn.execute(self._schema())
            conn.commit()

    def upsert_doc(self, table: str, title: str, body: str, metadata: dict[str, Any] | None = None):
        if table not in {"inventory", "todos", "calendar_events", "knowledge"}:
            raise ValueError(f"unsupported table: {table}")
        meta = json.dumps(metadata or {})
        emb = embed_text(f"{title} {body}")
        with self.connect() as conn:
            row = conn.execute(
                f"insert into {table} (title, body, metadata, embedding) values (%s,%s,%s,%s) returning id",
                (title, body, meta, emb),
            ).fetchone()
            conn.commit()
            return row[0]

    def search(self, table: str, query: str, limit: int = 5):
        if table not in {"inventory", "todos", "calendar_events", "knowledge"}:
            raise ValueError(f"unsupported table: {table}")
        vector = self._vector_literal(embed_text(query))
        with self.connect() as conn:
            rows = conn.execute(
                f"select id,title,body,metadata,1-(embedding <=> %s::vector) score from {table} order by embedding <=> %s::vector limit %s",
                (vector, vector, limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        names = ["inventory", "todos", "calendar_events", "knowledge"]
        with self.connect() as conn:
            counts = {name: conn.execute(f"select count(*) from {name}").fetchone()[0] for name in names}
        return {"tables": counts, "vector_dim": DIM}

    @staticmethod
    def _row(row):
        metadata = row[3]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return {"id": row[0], "title": row[1], "body": row[2], "metadata": metadata, "score": float(row[4])}

    @staticmethod
    def _vector_literal(values: list[float]) -> str:
        return "[" + ",".join(str(value) for value in values) + "]"

    @staticmethod
    def _schema() -> str:
        return f"""
        create table if not exists inventory (
          id bigserial primary key, title text not null, body text not null,
          metadata jsonb not null default '{{}}', embedding vector({DIM}) not null,
          created_at timestamptz not null default now());
        create table if not exists todos (like inventory including all);
        create table if not exists calendar_events (like inventory including all);
        create table if not exists knowledge (like inventory including all);
        """
