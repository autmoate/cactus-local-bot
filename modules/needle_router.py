from dataclasses import dataclass
from typing import Any

try:
    import needle
except Exception:  # pragma: no cover
    needle = None


@dataclass
class Proposal:
    tool: str | None
    arguments: dict[str, Any]
    confidence: float | None
    reasoning: str
    raw: dict[str, Any]

    @property
    def has_call(self) -> bool:
        return bool(self.tool)


class NeedleRouter:
    def __init__(self, tools, threshold: float, tool_index_path: str):
        self.threshold = threshold
        self.ready = False
        self.agent = None
        self.error = None
        if needle:
            try:
                self.agent = needle.Needle(tools=tools, tool_index_path=tool_index_path)
                self.ready = True
            except Exception as exc:
                self.error = str(exc)

    def propose(self, text: str) -> Proposal:
        if self.agent:
            self.agent.reset()
            response = self.agent.complete(text)
            calls = response.get("function_calls") or []
            if not calls:
                return Proposal(None, {}, response.get("confidence"), response.get("reasoning", ""), response)
            call = calls[0]
            return Proposal(
                call.get("name"), call.get("arguments", {}), response.get("confidence"), response.get("reasoning", ""), response
            )
        return self._heuristic(text)

    def should_execute(self, proposal: Proposal) -> bool:
        if not proposal.has_call:
            return False
        return proposal.confidence is None or proposal.confidence >= self.threshold

    def _heuristic(self, text: str) -> Proposal:
        lower = text.lower()
        if "todo" in lower or "aufgabe" in lower:
            return Proposal("create_todo", {"title": text}, 0.8, "heuristic matched todo intent", {})
        if "invent" in lower or "lager" in lower:
            return Proposal("add_inventory", {"name": text[:80], "description": text}, 0.8, "heuristic matched inventory intent", {})
        if "kalender" in lower or "termin" in lower:
            return Proposal("add_calendar_event", {"title": text[:80], "starts_at": text}, 0.8, "heuristic matched calendar intent", {})
        if "wissen" in lower or "knowledge" in lower or "speichere" in lower:
            return Proposal("add_knowledge", {"title": text[:80], "body": text}, 0.8, "heuristic matched knowledge intent", {})
        if "suche" in lower or "search" in lower:
            return Proposal("search_records", {"table": "knowledge", "query": text, "limit": 5}, 0.8, "heuristic matched search intent", {})
        if "db" in lower or "stats" in lower:
            return Proposal("db_stats", {}, 0.9, "heuristic matched database status", {})
        return Proposal(None, {}, 0.0, "no local tool intent detected", {})
