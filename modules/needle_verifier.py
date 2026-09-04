try:
    import needle
except Exception:  # pragma: no cover
    needle = None


def _norm_args(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, str):
            import re
            v = re.sub(r"\s+", " ", v).strip()
        out[k] = v
    return out


_SEMANTIC = ("due_at", "starts_at", "end_at", "quantity", "table", "horizon", "area", "limit", "in_min")


def _canonical(value):
    if isinstance(value, str):
        s = value.strip().replace("Z", "+00:00")
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                from modules.timesync import _TZ
                dt = dt.replace(tzinfo=_TZ)
            return dt
        except ValueError:
            from modules.timesync import resolve_dt
            resolved = resolve_dt(s)
            if resolved:
                return resolved
    return value


class NeedleVerifier:
    def __init__(self, tools, tool_index_path: str, system: str = ""):
        self.tools = tools
        self.tool_index_path = tool_index_path
        self.system = system
        self.ready = False
        self.agent = None
        self.error: str | None = None

    def ensure(self) -> bool:
        if needle is None:
            self.error = "needle not installed"
            return False
        if self.agent is not None:
            return True
        try:
            self.agent = needle.Needle(tools=self.tools, system=self.system,
                                       tool_index_path=self.tool_index_path)
            self.ready = True
            self.error = None
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def check(self, english_instruction: str) -> dict:
        if not self.ensure():
            return {"ok": False, "error": self.error}
        try:
            self.agent.reset()
            response = self.agent.complete(english_instruction)
            calls = response.get("function_calls") or []
            if not calls:
                return {"ok": False, "reason": "no call", "confidence": response.get("confidence")}
            call = calls[0]
            return {
                "ok": True,
                "tool": call.get("name"),
                "args": call.get("arguments", {}) or {},
                "confidence": response.get("confidence"),
                "reasoning": response.get("reasoning", ""),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def agreed(gemma_tool: str, gemma_args: dict, needle_call: dict) -> tuple[bool, str]:
        if not needle_call.get("ok"):
            return False, f"needle o.k.-Fehler: {needle_call.get('error') or needle_call.get('reason')}"
        if gemma_tool != needle_call["tool"]:
            return False, f"Tool-Divergenz: gemma={gemma_tool} vs needle={needle_call['tool']}"
        g = _norm_args(gemma_args)
        n = _norm_args(needle_call.get("args") or {})
        diff = []
        for k in _SEMANTIC:
            if k in n:
                if k not in g:
                    diff.append(f"{k} fehlt bei gemma")
                elif _canonical(g[k]) != _canonical(n[k]) and str(_canonical(g[k])) != str(_canonical(n[k])):
                    diff.append(f"{k}: {g[k]!r} vs {n[k]!r}")
        if diff:
            return False, "Argumente weichen ab: " + "; ".join(diff)
        return True, "agreed"
