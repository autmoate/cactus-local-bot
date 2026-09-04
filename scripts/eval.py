"""Eval-Runner: goldene Fälle gegen die laufende Pipeline.
Voraussetzung: cactus serve läuft (CACTUS_AUTO_START übernimmt launch.py).
Aufruf:  uv run python scripts/eval.py [--filter teilstring]
"""
import importlib.util
import sys
from datetime import datetime, timezone

from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
sys.path.insert(0, ".")

import eval_cases  # noqa: E402

_spec = importlib.util.spec_from_file_location("tui_app", "tui-app.py")
tui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tui)

from modules.config import load_config  # noqa: E402
from modules.embeddings import EmbeddingClient  # noqa: E402
from modules.postgres_store import PostgresStore, _parse_dt  # noqa: E402
from modules.tool_catalog import ToolCatalog, WRITE_TOOLS  # noqa: E402
from modules.cactus_engine import CactusEngine  # noqa: E402
from modules.needle_router import NeedleRouter  # noqa: E402
from modules.interpreter import GemmaInterpreter  # noqa: E402
from modules.needle_verifier import NeedleVerifier  # noqa: E402
from modules.memory import Memory  # noqa: E402
from modules.reflect import Reflect  # noqa: E402
from modules.scheduler import Scheduler  # noqa: E402
from modules.state import AppState  # noqa: E402
from modules.websearch import WebSearch  # noqa: E402
from modules.pipeline import process  # noqa: E402
from modules.timesync import _TZ  # noqa: E402


def build_runtime():
    cfg = load_config()
    embed = EmbeddingClient(cfg.cactus_base_url, model=cfg.embed_model or None)
    store = PostgresStore(cfg.database_url, embed=embed, dim=cfg.embed_dim)
    store.init()
    with store.connect() as con:
        con.execute("truncate inventory, todos, calendar_events, knowledge, messages, facts, "
                    "graph_nodes, graph_edges, events, event_changes restart identity cascade")
        con.commit()
    catalog = ToolCatalog(store, searcher=WebSearch(cfg.searxng_url))
    router = NeedleRouter(catalog.tools, cfg.confidence_threshold, cfg.tool_index_path)
    router.seed()
    cactus = CactusEngine(cfg.cactus_base_url)
    interpreter = GemmaInterpreter(cactus).bind(catalog.schemas())
    rt = tui.Runtime(AppState(), router, cactus, catalog, store,
                     Memory(store), Scheduler(store, True, 90),
                     cfg.confidence_threshold, cfg.behavior, None, True,
                     Reflect(store), interpreter, None, True)
    rt.trigger = cfg.trigger
    rt.followup = False  # Eval prüft Routing/Fixes, kein Multi-Intent
    return rt, store


class WriteAttempt(Exception):
    pass


def _raising_confirm(best):
    """Für NOWRITE-Fälle: jeder Write-Vorschlag ist ein Fehlschlag."""
    raise WriteAttempt(best.get("name"))


def _spy(catalog, recorded):
    """Zeichnet jeden Call auf und führt ihn wirklich aus (Produktionspfad, echte DB-Zustände)."""
    original = catalog.execute

    def spy(name, args):
        recorded.append((name, dict(args)))
        return original(name, args)

    catalog.execute = spy


def check(kind, args, spec) -> bool:
    if kind == "contains":
        return spec[2].lower() in str(args.get(spec[1], "")).lower()
    if kind == "eq":
        return str(args.get(spec[1], "")).lower() == str(spec[2]).lower()
    if kind == "within_min" and spec[1] not in args and args.get("in_min") is not None:
        return abs(float(args["in_min"]) - spec[2]) <= spec[3]
    at = _parse_dt(args.get(spec[1], ""))
    if at is None:
        from modules.timesync import resolve_dt
        at = resolve_dt(str(args.get(spec[1], "")))
        if at is None:
            return False
    if kind == "within_min":
        delta = (at - datetime.now(timezone.utc)).total_seconds() / 60
        return abs(delta - spec[2]) <= spec[3]
    at = at.astimezone(_TZ)
    if kind == "local_hm":
        return (at.hour, at.minute) == (spec[2], spec[3])
    if kind == "weekday_hm":
        ahead = (at.date() - datetime.now(_TZ).date()).days
        return at.weekday() == spec[2] and (at.hour, at.minute) == (spec[3], spec[4]) and 0 <= ahead <= 14
    return False


def main() -> int:
    import sys as _sys
    flt = _sys.argv[_sys.argv.index("--filter") + 1] if "--filter" in _sys.argv else ""
    rt, store = build_runtime()
    if not rt.cactus.online():
        print("cactus serve ist offline — bitte zuerst starten (scripts/launch.py).")
        return 2
    results = []
    for name, text, expect, checks, pre in eval_cases.CASES:
        if flt and flt not in name:
            continue
        with store.connect() as con:  # pro Fall sauberer Zustand (wie frischer Agent)
            con.execute("truncate inventory, todos, calendar_events, knowledge, messages, facts, "
                        "graph_nodes, graph_edges, events, event_changes restart identity cascade")
            con.commit()
        recorded: list = []
        _spy(rt.catalog, recorded)
        nowrite = expect == "NOWRITE"

        def log(_line):
            pass

        try:
            for pre_text in pre:
                pre_out = process(pre_text, rt, log, lambda _b: True, rt.trigger)
                rt.memory.record_turn(pre_text, pre_out.get("decision", "done"),
                                      reply=pre_out.get("text") or None)
            recorded.clear()  # nur der Haupt-Satz zählt
            out = process(text, rt, log,
                          _raising_confirm if expect in ("NOWRITE", "GATEDWRITE") else (lambda _b: True),
                          rt.trigger)
            ok, why = _judge(expect, checks, recorded, out)
        except WriteAttempt:
            if expect == "GATEDWRITE":
                ok, why = True, "gated (Vorschlag, wartet auf Freigabe)"
            else:
                ok, why = False, "unerwarteter Write"
        except Exception as exc:
            ok, why = False, f"fehler: {str(exc)[:80]}"
        results.append((name, ok, why))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {why}", flush=True)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} bestanden")
    return 0 if passed == len(results) else 1


def _judge(expect, checks, recorded, out) -> tuple[bool, str]:
    if isinstance(expect, tuple) and expect and expect[0] == "TOOL_IN":
        if not recorded:
            return False, "kein Call"
        tool, args = recorded[0]
        if tool not in expect[1]:
            return False, f"tool={tool} (erwartet eins aus {expect[1]})"
        for spec in checks:
            if not check(spec[0], args, spec):
                return False, f"{spec[0]} fehlgeschlagen: {spec} args={args}"
        return True, tool
    if expect == "SILENT":
        return (True, "still") if out.get("decision") == "silent" and not recorded else (False, f"calls: {recorded} decision={out.get('decision')}")
    if expect == "ANSWER":
        return (True, "antwort") if out.get("decision") == "answer" and not recorded else (False, f"calls: {recorded} decision={out.get('decision')}")
    if expect == "NOWRITE":
        bad = [name for name, _ in recorded if name in WRITE_TOOLS]
        return (False, f"write: {bad}") if bad else (True, "")
    if expect == "GATEDWRITE":
        bad = [name for name, _ in recorded if name in WRITE_TOOLS]
        return (True, "read-only") if not bad else (False, f"ungated write: {bad}")
    if not recorded:
        return False, "kein Call"
    tool, args = recorded[0]
    if tool != expect:
        return False, f"tool={tool} (erwarte {expect})"
    for spec in checks:
        if not check(spec[0], args, spec):
            return False, f"{spec[0]} fehlgeschlagen: {spec} args={args}"
    return True, ""


if __name__ == "__main__":
    raise SystemExit(main())
