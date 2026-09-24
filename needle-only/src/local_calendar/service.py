"""AtomicService: model interpretation separated from DB mutation (plan §10-§14).

prepare() classifies a request into exactly one of:
  NoAction · ReadAction · WriteProposal · MultiActionRejected · AmbiguousAction
Reads execute immediately under the RequestContext scope; writes create a
persisted action_proposal (token, TTL, actor/chat binding, target fingerprint)
that is only committed on an explicit confirm. Adapter-neutral — Telegram (or a
later PWA/Matrix) only renders the decisions and proposals.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, field

import needle

from . import calendar as cal
from .agent import Agent, build_tools, execute_call
from .identity import RequestContext

READ_TOOLS = {"calendar_list", "calendar_find_slot"}
WRITE_TOOLS = {"calendar_create", "calendar_move", "calendar_delete"}
MULTI_REJECT = ("📌 Ich führe Kalenderaktionen einzeln aus.\n"
                "Bitte sende sie nacheinander.")
AMBIGUOUS = ("📌 Ich finde mehrere passende Termine.\n"
             "Bitte nenne Datum oder Uhrzeit in einer neuen Nachricht.")
NO_ACTION = "📌 Kein Kalender-Befehl erkannt."
TTL_SECONDS = 300


def fingerprint(meta: dict) -> str:
    key = {k: meta.get(k) for k in ("title", "start", "end", "all_day")}
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()
                          ).hexdigest()


@dataclass
class Decision:
    kind: str                       # no_action|read|write_proposal|multi_rejected|ambiguous|error
    tool: str = ""
    message: str = ""
    read: dict | None = None
    proposal: dict | None = None
    calls: list = field(default_factory=list)
    resolved: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


class AtomicService:
    """One Needle session; interpretation only, no mutation outside confirm()."""

    def __init__(self, store, weights: str | None = None, system: str | None = None):
        self.store = store
        self.tools = build_tools(store)
        kwargs: dict = {"tools": list(self.tools.values()),
                        "system": system or f"date: {cal.now():%Y-%m-%d %a %H:%M}"}
        if weights:
            kwargs["weights"] = weights
        self.needle = needle.Needle(**kwargs)
        self.lock = threading.Lock()  # one engine call at a time (plan §15)

    def interpret(self, text: str) -> list[dict]:
        t = (text or "").strip().rstrip(".!?;:,")
        if not t:
            return []
        with self.lock:
            self.needle.reset()
            resp = self.needle.complete(t) or {}
        return Agent._merge_calls(resp.get("function_calls") or [])

    def prepare(self, text: str, ctx: RequestContext) -> Decision:
        calls = self.interpret(text)
        if not calls:
            return Decision("no_action", message=NO_ACTION)
        if len(calls) > 1:
            return Decision("multi_rejected",
                            calls=[c.get("name") for c in calls], message=MULTI_REJECT)
        call = calls[0]
        name = call.get("name", "")
        args = dict(call.get("arguments") or {})
        if name in READ_TOOLS:
            out = execute_call(self.store, name, args, text, scope=ctx)
            if not out.get("ok"):
                return Decision("error", tool=name, message=out["message"])
            return Decision("read", tool=name, message=out["message"], read=out)
        if name in WRITE_TOOLS:
            return self._propose(name, args, text, ctx)
        return Decision("no_action", message=NO_ACTION)

    def _propose(self, name: str, args: dict, text: str,
                 ctx: RequestContext) -> Decision:
        out = execute_call(self.store, name, args, text, commit=False, scope=ctx)
        if not out.get("ok"):
            kind = "ambiguous" if "Mehrere Einträge" in out["message"] else "error"
            return Decision(kind, tool=name, message=out["message"])
        resolved = out["resolved"]
        target_id, fp, meta = None, None, None
        if name == "calendar_move":
            meta = resolved.get("before")
        elif name == "calendar_delete":
            meta = resolved
        if meta:
            target_id, fp = meta.get("id"), fingerprint(meta)
            if meta.get("calendar_id") not in (None, ctx.target_calendar_id):
                return Decision("error", tool=name, message=(
                    "📌 Dieser Termin liegt in einem anderen Kalender. "
                    "Bitte im passenden Chat bearbeiten."))
        token = secrets.token_urlsafe(9)
        self.store.create_proposal(token, ctx.actor_person_id, ctx.chat_id,
                                   ctx.target_calendar_id, name, args, resolved,
                                   target_id, fp, context=text, ttl_seconds=TTL_SECONDS)
        return Decision("write_proposal", tool=name, message=out["message"],
                        proposal={"token": token, "tool": name, "args": args,
                                  "resolved": resolved}, resolved=resolved,
                        warnings=list(out.get("warnings") or []))

    def confirm(self, token: str, ctx: RequestContext) -> dict:
        p = self.store.get_proposal(token)
        if not p or p["status"] != "pending":
            return {"ok": False, "message": "📌 Vorschlag ungültig oder bereits verwendet."}
        if p["actor_person_id"] != ctx.actor_person_id or p["chat_id"] != ctx.chat_id:
            return {"ok": False, "message": "📌 Nicht dein Vorschlag."}
        if cal.now() > cal.datetime.fromisoformat(p["expires_at"]):
            self.store.set_proposal_status(token, "expired")
            return {"ok": False, "message": "📌 Der Vorschlag ist abgelaufen. Bitte erneut senden."}
        if p["target_event_id"]:
            ev = self.store.get(p["target_event_id"])
            if ev is None or \
                    fingerprint(ev.model_dump(mode="json")) != p["target_fingerprint"]:
                self.store.set_proposal_status(token, "stale")
                return {"ok": False, "message": ("📌 Der Kalender hat sich inzwischen "
                                                 "geändert. Bitte erneut senden.")}
        out = execute_call(self.store, p["tool_name"],
                           json.loads(p["args_json"]), p.get("context") or "",
                           commit=True, scope=ctx)
        self.store.set_proposal_status(token, "done" if out.get("ok") else "failed")
        return out

    def cancel(self, token: str, ctx: RequestContext) -> None:
        p = self.store.get_proposal(token)
        if p and p["actor_person_id"] == ctx.actor_person_id \
                and p["chat_id"] == ctx.chat_id:
            self.store.set_proposal_status(token, "cancelled")


def preview_lines(decision: Decision, store, ctx: RequestContext) -> list[str]:
    """User-facing preview for a write proposal (plan §12/§26). Warnings inform
    but never block confirm — the confirm button stays available."""
    name, r = decision.tool, decision.resolved
    cal_name = "Gruppe" if ctx.is_group else "Persönlich"
    out: list[str]
    if name == "calendar_create":
        who = r.get("participants") or []
        title = r.get("title", "")
        head = "📌 Neuer Gruppentermin" if ctx.is_group else "📌 Neuer Termin"
        out = [head, title, _when(r), f"Kalender: {cal_name}"]
        if who:
            out.append("Teilnehmer: " + ", ".join(who))
    elif name == "calendar_move":
        b, a = r.get("before", {}), r.get("after", {})
        out = ["📌 Termin verschieben", b.get("title", ""),
               f"{_when(b)} → {_when(a)}"]
    elif name == "calendar_delete":
        out = ["📌 Termin löschen", r.get("title", ""), _when(r)]
    else:
        out = ["📌 Vorschlag"]
    for warning in (decision.warnings or []):
        out.append(f"⚠️ {warning}")
    return out


def _when(meta: dict) -> str:
    try:
        s = cal.datetime.fromisoformat(meta["start"])
        e = cal.datetime.fromisoformat(meta["end"])
    except (KeyError, ValueError, TypeError):
        return ""
    if meta.get("all_day"):
        last = e - cal.timedelta(days=1)
        return (f"{cal.WEEKDAYS_DE[s.weekday()]} {s:%d.%m.}"
                if last.date() == s.date()
                else f"{s:%d.%m.} – {last:%d.%m.} (ganztägig)")
    return f"{cal.WEEKDAYS_DE[s.weekday()]} {s:%d.%m.} {s:%H:%M}–{e:%H:%M}"
