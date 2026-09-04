import json
from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from modules.embeddings import EmbeddingClient, HASH_DIM

DATA_TABLES = ("inventory", "todos", "calendar_events", "knowledge")


def _parse_dt(value: str):
    from datetime import datetime
    text = str(value).strip().replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M%z",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=__import__("datetime").timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=__import__("datetime").timezone.utc)
    except ValueError:
        return None


class PostgresStore:
    def __init__(self, url: str, embed: EmbeddingClient | None = None, dim: int = 0):
        self.url = url
        self.embed = embed or EmbeddingClient("")
        self.dim = dim or HASH_DIM
        if embed is not None and dim:
            embed._dim = dim

    def connect(self):
        conn = psycopg.connect(self.url)
        register_vector(conn)
        return conn

    def ping(self) -> bool:
        try:
            with self.connect() as conn:
                return conn.execute("select 1").fetchone()[0] == 1
        except Exception:
            return False

    def init(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("create extension if not exists vector")
        with self.connect() as conn:
            existing = self._existing_dim(conn)
            if self.dim == HASH_DIM and existing:
                self.dim = existing
                self.embed._dim = existing
            elif self.dim != HASH_DIM and existing and existing != self.dim:
                conn.execute(
                    "drop table if exists inventory, todos, calendar_events, knowledge, "
                    "messages, facts, intent_exemplars, graph_nodes cascade"
                )
                existing = None
            if self.dim == HASH_DIM and not existing:
                self.dim = self.embed.dimension() or len(self.embed.embed("__probe__"))
            conn.execute(self._schema())
            conn.commit()

    def _existing_dim(self, conn) -> int | None:
        try:
            row = conn.execute(
                "select a.atttypmod from pg_class c "
                "join pg_attribute a on a.attrelid = c.oid "
                "where c.relname = 'inventory' and a.attname = 'embedding'"
            ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def _schema(self) -> str:
        d = self.dim
        return f"""
        create table if not exists inventory (
          id bigserial primary key, title text not null, body text not null,
          metadata jsonb not null default '{{}}', embedding vector({d}) not null,
          created_at timestamptz not null default now());
        create table if not exists todos (like inventory including all);
        create table if not exists calendar_events (like inventory including all);
        create table if not exists knowledge (like inventory including all);
        create table if not exists messages (
          id bigserial primary key, role text not null, text text not null,
          embedding vector({d}), meta jsonb not null default '{{}}',
          space text not null default 'default',
          created_at timestamptz not null default now());
        create table if not exists facts (
          id bigserial primary key, subject text not null, predicate text not null,
          object text not null, confidence real not null default 0.7,
          source text not null default 'manual', embedding vector({d}) not null,
          space text not null default 'default',
          created_at timestamptz not null default now(),
          last_seen timestamptz not null default now());
        alter table facts add column if not exists valid_from timestamptz not null default now();
        alter table facts add column if not exists valid_to timestamptz;
        alter table facts add column if not exists superseded_by bigint;
        create index if not exists messages_created_idx on messages (created_at);
        create index if not exists facts_active_idx on facts (space, valid_to);
        create table if not exists agent_profile (
          scope text not null, section text not null, content jsonb not null default '{{}}',
          version int not null default 1,
          updated_at timestamptz not null default now(),
          primary key (scope, section));
        create table if not exists user_profile (
          scope text not null, section text not null, content jsonb not null default '{{}}',
          confirmed boolean not null default false,
          updated_at timestamptz not null default now(),
          primary key (scope, section));
        create table if not exists agent_changes (
          id bigserial primary key, scope text not null default 'default',
          area text not null, note text not null default '',
          old_value jsonb, new_value jsonb,
          created_at timestamptz not null default now());
        create table if not exists graph_nodes (
          id bigserial primary key, space text not null default 'default',
          subject text not null, embedding vector({d}),
          properties jsonb not null default '{{}}',
          created_at timestamptz not null default now(),
          unique (space, subject));
        create table if not exists graph_edges (
          id bigserial primary key, space text not null default 'default',
          src bigint not null references graph_nodes(id) on delete cascade,
          dst bigint not null references graph_nodes(id) on delete cascade,
          rel text not null, properties jsonb not null default '{{}}',
          created_at timestamptz not null default now(),
          unique (src, dst, rel));
        create table if not exists intent_exemplars (
          intent text not null, phrase text not null, embedding vector({d}) not null,
          primary key (intent, phrase));
        create table if not exists events (
          id bigserial primary key, space text not null default 'default',
          title text not null, start_at timestamptz, end_at timestamptz,
          urgency text not null default 'normal', repeats text not null default '',
          status text not null default 'geplant', location text not null default '',
          participants jsonb not null default '{{}}', notes text not null default '',
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now());
        create table if not exists event_changes (
          id bigserial primary key, space text not null default 'default',
          event_id bigint not null, note text not null default '',
          old_status text, new_status text,
          created_at timestamptz not null default now());
        """

    def _ensure_dim(self) -> None:
        real = self.embed.dimension()
        if real is None:
            self.embed.embed("__probe__")
            real = self.embed.dimension()
        if real is not None and real != self.dim:
            self.init()

    def upsert_doc(self, table: str, title: str, body: str, metadata: dict[str, Any] | None = None):
        self._ensure_dim()
        if table not in DATA_TABLES:
            raise ValueError(f"unsupported table: {table}")
        meta = json.dumps(metadata or {})
        emb = self.embed.embed(f"{title} {body}")
        with self.connect() as conn:
            row = conn.execute(
                f"insert into {table} (title, body, metadata, embedding) values (%s,%s,%s,%s) returning id",
                (title, body, meta, emb),
            ).fetchone()
            conn.commit()
            return row[0]

    def search(self, table: str, query: str, limit: int = 5):
        self._ensure_dim()
        if table not in DATA_TABLES:
            raise ValueError(f"unsupported table: {table}")
        vector = self._vector_literal(self.embed.embed(query))
        with self.connect() as conn:
            rows = conn.execute(
                f"select id,title,body,metadata,1-(embedding <=> %s::vector) score from {table} "
                f"order by embedding <=> %s::vector limit %s",
                (vector, vector, limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            counts = {name: conn.execute(f"select count(*) from {name}").fetchone()[0] for name in DATA_TABLES}
            counts["messages"] = conn.execute("select count(*) from messages").fetchone()[0]
            counts["facts"] = conn.execute("select count(*) from facts").fetchone()[0]
        return {"tables": counts, "vector_dim": self.dim}

    def log_message(self, role: str, text: str, meta: dict[str, Any] | None = None,
                    space: str = "default", embed_vec: bool = True) -> int:
        if embed_vec:
            self._ensure_dim()
            emb = self.embed.embed(text)
        else:
            emb = None
        with self.connect() as conn:
            row = conn.execute(
                "insert into messages (role, text, embedding, meta, space) values (%s,%s,%s,%s,%s) returning id",
                (role, text, emb, json.dumps(meta or {}), space),
            ).fetchone()
            conn.commit()
            return row[0]

    def recall(self, text: str, limit: int = 5, space: str = "default") -> list[dict[str, Any]]:
        self._ensure_dim()
        vector = self._vector_literal(self.embed.embed(text))
        with self.connect() as conn:
            rows = conn.execute(
                "select id,role,text,meta,1-(embedding <=> %s::vector) score from messages "
                "where space = %s and embedding is not null "
                "order by embedding <=> %s::vector limit %s",
                (vector, space, vector, limit),
            ).fetchall()
        return [self._msg_row(row) for row in rows]

    def active_facts(self, limit: int = 20) -> list[dict[str, Any]]:
        from modules.facts import active_facts
        return active_facts(self, limit)

    def recall_facts(self, text: str, limit: int = 5, space: str = "default") -> list[dict[str, Any]]:
        from modules.facts import recall_facts
        return recall_facts(self, text, limit, space)

    def get_profile(self, scope: str, section: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "select content from agent_profile where scope = %s and section = %s",
                (scope, section),
            ).fetchone()
            return (row[0] if row else {}) or {}

    def set_profile(self, scope: str, section: str, content: dict[str, Any], note: str = "") -> None:
        with self.connect() as conn:
            prev = conn.execute(
                "select content from agent_profile where scope = %s and section = %s",
                (scope, section),
            ).fetchone()
            old = prev[0] if prev else None
            conn.execute(
                "insert into agent_profile (scope, section, content) values (%s,%s,%s) "
                "on conflict (scope, section) do update set content = excluded.content, "
                "version = agent_profile.version + 1, updated_at = now()",
                (scope, section, json.dumps(content)),
            )
            self._log_change(conn, scope, f"agent_profile/{section}", note, old, content)
            conn.commit()

    def _log_change(self, conn, scope: str, area: str, note: str, old: Any, new: Any) -> None:
        conn.execute(
            "insert into agent_changes (scope, area, note, old_value, new_value) values (%s,%s,%s,%s,%s)",
            (scope, area, note, json.dumps(old) if old is not None else None,
             json.dumps(new) if new is not None else None),
        )

    def log_change(self, scope: str, area: str, note: str, old: Any, new: Any) -> None:
        with self.connect() as conn:
            self._log_change(conn, scope, area, note, old, new)
            conn.commit()

    def list_changes(self, scope: str = "default", limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select id,area,note,old_value,new_value,created_at from agent_changes "
                "where scope = %s order by id desc limit %s",
                (scope, limit),
            ).fetchall()
        return [dict(zip(("id", "area", "note", "old", "new", "at"), r)) for r in rows]

    def due_items(self, table: str, start, end) -> list[dict[str, Any]]:
        col = "due_at" if table == "todos" else "starts_at"
        with self.connect() as conn:
            rows = conn.execute(
                f"select id,title,metadata from {table} order by created_at asc limit 60"
            ).fetchall()
        items = []
        for rid, title, meta in rows:
            m = meta if isinstance(meta, dict) else (json.loads(meta) if isinstance(meta, str) else {})
            when = m.get(col) or m.get("starts_at") or ""
            at = _parse_dt(when)
            if at is not None and start <= at <= end:
                items.append({"id": rid, "title": title, "at": at})
        return items

    def prune_stale_facts(self, older_than_days: int = 30, max_conf: float = 0.5) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "delete from facts where valid_to is not null "
                "and valid_to < now() - make_interval(days => %s) returning id",
                (older_than_days,),
            ).fetchall()
            conn.commit()
            return len(row)

    def last_area_change(self, area: str):
        with self.connect() as conn:
            row = conn.execute(
                "select created_at from agent_changes where area = %s order by id desc limit 1",
                (area,),
            ).fetchone()
            return row[0] if row else None

    def upcoming(self, table: str, start, end, limit: int = 10,
                 space: str = "default") -> list[dict[str, Any]]:
        col = "due_at" if table == "todos" else "starts_at"
        with self.connect() as conn:
            rows = conn.execute(
                f"select id,title,metadata from {table} order by created_at asc limit 200"
            ).fetchall()
        items = []
        for rid, title, meta in rows:
            m = meta if isinstance(meta, dict) else (json.loads(meta) if isinstance(meta, str) else {})
            when = m.get(col) or ""
            at = _parse_dt(when)
            if at is not None and (at.tzinfo is None or True) and start <= at <= end:
                items.append({"id": rid, "title": title, "at": at, "metadata": m})
        items.sort(key=lambda i: i["at"])
        return items[:limit]

    def purge_old_messages(self, hours: int = 24) -> int:
        """Roh-Chat-Retention: löscht Nachrichten älter als `hours` (Datenschutz)."""
        with self.connect() as conn:
            rows = conn.execute(
                "delete from messages where created_at < now() - make_interval(hours => %s) returning id",
                (hours,),
            ).fetchall()
            conn.commit()
            return len(rows)

    def recent_messages(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select id,role,text,meta,created_at from messages order by id desc limit %s", (limit,),
            ).fetchall()
        return [dict(zip(("id", "role", "text", "meta", "at"), r)) for r in rows]

    def add_node(self, subject: str, label: str = "", properties: dict[str, Any] | None = None,
                 space: str = "default") -> int:
        self._ensure_dim()
        emb = self.embed.embed(f"{subject} {label}")
        with self.connect() as conn:
            row = conn.execute(
                "insert into graph_nodes (space, subject, embedding, properties) values (%s,%s,%s,%s) "
                "on conflict (space, subject) do update set properties = excluded.properties, "
                "embedding = excluded.embedding returning id",
                (space, subject, emb, json.dumps(properties or {})),
            ).fetchone()
            conn.commit()
            return row[0]

    def add_edge(self, src_subject: str, dst_subject: str, rel: str,
                 properties: dict[str, Any] | None = None, space: str = "default") -> None:
        src = self.add_node(src_subject, space=space)
        dst = self.add_node(dst_subject, space=space)
        with self.connect() as conn:
            conn.execute(
                "insert into graph_edges (space, src, dst, rel, properties) values (%s,%s,%s,%s,%s) "
                "on conflict (src, dst, rel) do update set properties = excluded.properties",
                (space, src, dst, rel, json.dumps(properties or {})),
            )
            conn.commit()

    def neighbors(self, subject: str, space: str = "default", limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            node = conn.execute(
                "select id from graph_nodes where space = %s and subject = %s", (space, subject)
            ).fetchone()
            if not node:
                return []
            rows = conn.execute(
                "select e.rel, n.subject from graph_edges e "
                "join graph_nodes n on n.id = e.dst "
                "where e.src = %s and n.space = %s limit %s", (node[0], space, limit),
            ).fetchall()
        return [{"rel": r, "subject": s} for r, s in rows]

    def save_intent_exemplars(self, phrases: dict[str, list[str]],
                              vectors: dict[str, list[list[float]]]) -> None:
        with self.connect() as conn:
            conn.execute("delete from intent_exemplars")
            for intent, phrs in phrases.items():
                for i, phrase in enumerate(phrs):
                    if i < len(vectors.get(intent, [])):
                        conn.execute(
                            "insert into intent_exemplars (intent, phrase, embedding) values (%s,%s,%s)",
                            (intent, phrase, vectors[intent][i]),
                        )
            conn.commit()

    def load_intent_exemplars(self) -> dict[str, list[list[float]]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select intent, phrase, embedding from intent_exemplars order by intent"
            ).fetchall()
        out: dict[str, list[list[float]]] = {}
        for intent, _phrase, emb in rows:
            vec = emb.to_list() if hasattr(emb, "to_list") else list(emb)
            out.setdefault(intent, []).append(vec)
        return out

    def add_event(self, title: str, start_at, end_at=None, urgency: str = "normal",
                  repeats: str = "", notes: str = "", location: str = "",
                  participants: list[str] | None = None, space: str = "default") -> int:
        self._ensure_dim()
        end_at = end_at or None
        with self.connect() as conn:
            row = conn.execute(
                "insert into events (space,title,start_at,end_at,urgency,repeats,notes,location,participants) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (space, title, start_at, end_at, urgency, repeats, notes,
                 location, json.dumps(participants or [])),
            ).fetchone()
            conn.commit()
            return row[0]

    def find_event(self, title: str, start_at=None, space: str = "default") -> int | None:
        """Findet bestehenden Termin (Titel-Teilmatch, ±1 Tag) — für Duplikatschutz."""
        with self.connect() as conn:
            if start_at is not None:
                row = conn.execute(
                    "select id from events where space = %s and title ilike %s "
                    "and abs(extract(epoch from (start_at - %s))) < 86400 limit 1",
                    (space, f"%{title}%", start_at),
                ).fetchone()
            else:
                row = conn.execute(
                    "select id from events where space = %s and title ilike %s limit 1",
                    (space, f"%{title}%"),
                ).fetchone()
            return row[0] if row else None

    def update_event(self, title: str, start_at=None, end_at=None, location: str | None = None,
                     notes: str | None = None, space: str = "default") -> dict:
        """Ändert einen bestehenden Termin (Teilmatch auf Titel) — ICS-orientiert."""
        with self.connect() as conn:
            row = conn.execute(
                "select id from events where space = %s and title ilike %s "
                "order by start_at asc limit 1", (space, f"%{title}%"),
            ).fetchone()
            if not row:
                return {"updated": False}
            sets, vals = [], []
            if start_at:
                sets.append("start_at = %s")
                vals.append(start_at)
            if end_at:
                sets.append("end_at = %s")
                vals.append(end_at)
            if location is not None:
                sets.append("location = %s")
                vals.append(location)
            if notes is not None:
                sets.append("notes = %s")
                vals.append(notes)
            if not sets:
                return {"updated": False, "id": row[0]}
            vals.append(row[0])
            conn.execute(
                f"update events set {', '.join(sets)}, updated_at = now() where id = %s", vals,
            )
            conn.execute(
                "insert into event_changes (space, event_id, note) values (%s, %s, 'update')",
                (space, row[0]),
            )
            conn.commit()
            return {"updated": True, "id": row[0]}

    def cancel_event(self, title: str, space: str = "default") -> dict:
        """Sagt einen Termin ab (status='abgesagt', aus Listen gefiltert)."""
        with self.connect() as conn:
            row = conn.execute(
                "select id from events where space = %s and title ilike %s "
                "and status <> 'abgesagt' order by start_at asc limit 1",
                (space, f"%{title}%"),
            ).fetchone()
            if not row:
                return {"cancelled": False}
            conn.execute("update events set status = 'abgesagt', updated_at = now() where id = %s", (row[0],))
            conn.execute(
                "insert into event_changes (space, event_id, note, old_status, new_status) "
                "values (%s, %s, 'cancel', 'geplant', 'abgesagt')", (space, row[0]),
            )
            conn.commit()
            return {"cancelled": True, "id": row[0]}

    def update_event_status(self, event_id: int, status: str, space: str = "default", note: str = "") -> bool:
        with self.connect() as conn:
            cur = conn.execute("select status from events where id = %s and space = %s", (event_id, space)).fetchone()
            if not cur:
                return False
            conn.execute("update events set status = %s, updated_at = now() where id = %s", (status, event_id))
            conn.execute(
                "insert into event_changes (space,event_id,note,old_status,new_status) values (%s,%s,%s,%s,%s)",
                (space, event_id, note, cur[0], status),
            )
            conn.commit()
            return True

    def list_events(self, start, end, space: str = "default", limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "select id,title,start_at,end_at,urgency,repeats,status,location,participants,notes "
                "from events where space=%s and status <> 'abgesagt' and start_at is not null "
                "and start_at >= %s and start_at <= %s order by start_at asc limit %s",
                (space, start, end, limit),
            ).fetchall()
        keys = ("id", "title", "start_at", "end_at", "urgency", "repeats", "status",
                "location", "participants", "notes")
        return [dict(zip(keys, r)) for r in rows]

    def update_inventory(self, title: str, quantity: int, space: str = "default") -> int:
        with self.connect() as conn:
            rows = conn.execute(
                "select id, metadata from inventory where title ilike %s limit 1",
                (f"%{title}%",),
            ).fetchall()
            if not rows:
                return 0
            rid, meta = rows[0]
            m = meta if isinstance(meta, dict) else (json.loads(meta) if isinstance(meta, str) else {})
            m["quantity"] = int(quantity)
            conn.execute("update inventory set metadata = %s, body = %s where id = %s",
                         (json.dumps(m), f"{quantity} Stück", rid))
            conn.commit()
            return rid

    def delete_todo(self, title: str, space: str = "default") -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "delete from todos where title ilike %s returning id", (f"%{title}%",)
            ).fetchall()
            conn.commit()
            return len(cur)

    def last_todo_title(self, space: str = "default") -> str | None:
        with self.connect() as conn:
            row = conn.execute("select title from todos order by id desc limit 1").fetchone()
            return row[0] if row else None

    def update_todo(self, title: str, due_at: str | None = None, notes: str | None = None,
                    space: str = "default") -> int:
        with self.connect() as conn:
            rows = conn.execute(
                "select id, metadata from todos where title ilike %s limit 1", (f"%{title}%",)
            ).fetchall()
            if not rows:
                return 0
            rid, meta = rows[0]
            m = meta if isinstance(meta, dict) else (json.loads(meta) if isinstance(meta, str) else {})
            if due_at:
                m["due_at"] = due_at
            if notes is not None:
                m["notes"] = notes
            conn.execute("update todos set metadata = %s where id = %s", (json.dumps(m), rid))
            conn.commit()
            return rid

    def graph_dump(self, space: str = "default", limit: int = 60) -> dict[str, Any]:
        with self.connect() as conn:
            nodes = conn.execute(
                "select id, subject from graph_nodes where space = %s order by id limit %s", (space, limit)
            ).fetchall()
            edges = conn.execute(
                "select n.subject, e.rel, m.subject from graph_edges e "
                "join graph_nodes n on n.id = e.src join graph_nodes m on m.id = e.dst "
                "where e.space = %s order by e.id limit %s", (space, limit),
            ).fetchall()
        return {"nodes": [{"id": i, "subject": s} for i, s in nodes],
                "edges": [{"src": a, "rel": r, "dst": b} for a, r, b in edges]}

    @staticmethod
    def _row(row):
        metadata = row[3]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return {"id": row[0], "title": row[1], "body": row[2], "metadata": metadata, "score": float(row[4])}

    @staticmethod
    def _msg_row(row):
        meta = row[3]
        if isinstance(meta, str):
            meta = json.loads(meta)
        return {"id": row[0], "role": row[1], "text": row[2], "meta": meta, "score": float(row[4])}

    @staticmethod
    def _vector_literal(values: list[float]) -> str:
        return "[" + ",".join(str(value) for value in values) + "]"
