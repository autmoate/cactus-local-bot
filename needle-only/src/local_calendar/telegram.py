"""Thin Telegram adapter (plan §17): Update -> Agent.handle() -> reply.

No second orchestration, no second calendar logic — Telegram shares the same
Agent, the same SQLite store and the same trace format as Gradio (plan §28,
§33). Security (plan §19): owner/allowlist only; unknown chats are ignored
without agent execution. Secrets come from the existing .env (same variable
names as the root project) and are never logged."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from . import calendar as cal
from . import render
from .agent import Agent
from .calendar import CalendarStore

_TRACES = Path(__file__).resolve().parents[2] / "data" / "traces.jsonl"
_ENV_PATH: Path | None = None


def _load_env() -> Path | None:
    """Reuse the existing workspace .env (needle-only/.env, then root .env)."""
    global _ENV_PATH
    for candidate in (Path(__file__).resolve().parents[2] / ".env",
                      Path(__file__).resolve().parents[3] / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            _ENV_PATH = candidate
            return candidate
    return None


def _monday(today: cal.date) -> cal.date:
    return today - cal.timedelta(days=today.weekday())


class TelegramBot:
    """One Agent instance; navigation state is per chat and purely numeric."""

    def __init__(self, agent: Agent, owner: int, allowed: set[int]):
        self.agent = agent
        self.owner = owner
        self.allowed = allowed
        self.week_offset: dict[int, int] = {}
        self.day_offset: dict[int, int] = {}
        self.people: dict[int, list[str]] = {}
        self.widget_msg: dict[int, int] = {}  # one live widget per chat

    # ----------------------------------------------------------- security
    def _allowed_chat(self, chat_id: int) -> bool:
        return chat_id == self.owner or chat_id in self.allowed

    def _pair_owner(self, chat_id: int) -> None:
        """Erstes /start wird Owner (Pairing, §19) und wird in der .env
        persistiert — solange TELEGRAM_OWNER_CHAT_ID leer ist. Es wird nur
        die numerische Chat-ID geschrieben, niemals der Token."""
        self.owner = chat_id
        if _ENV_PATH is None:
            return
        key = "TELEGRAM_OWNER_CHAT_ID="
        try:
            lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if line.startswith(key):
                    lines[i] = f"{key}{chat_id}"
                    break
            else:
                lines.append(f"{key}{chat_id}")
            _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass  # Pairing gilt für die laufende Session auch ohne Persistenz

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _plain(text: str | None) -> str:
        return (text or "").replace("**", "")

    async def _run_agent(self, text: str, session_id: str) -> dict:
        def work():
            final = None
            for trace in self.agent.handle(text, session_id=session_id):
                final = trace
            return final or {}
        return await asyncio.to_thread(work)

    @staticmethod
    def _executed_tools(trace: dict) -> set[str]:
        return {s["name"].split(" ", 1)[1] for s in trace.get("steps") or []
                if s["name"].startswith("execute ")}

    @staticmethod
    def _resolved(trace: dict, tool: str) -> dict:
        for s in reversed(trace.get("steps") or []):
            if s["name"].startswith(f"execute {tool}"):
                out = s.get("output") or {}
                return out.get("resolved") or {}
        return {}

    def _people_of(self, chat_id: int) -> list[str]:
        return self.people.get(chat_id, ["all"])

    def _nav_keyboard(self, chat_id: int) -> InlineKeyboardMarkup:
        names = ["Alle", "Ich"] + [p for p in
                                   self.agent.store.participant_names()
                                   if p != "Ich"]
        people = [InlineKeyboardButton(n, callback_data=f"people:{n}")
                  for n in names]
        rows = [people[i:i + 3] for i in range(0, len(people), 3)]  # plan §9
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("◀ Woche", callback_data="week:-1"),
             InlineKeyboardButton("Heute", callback_data="week:0"),
             InlineKeyboardButton("Woche ▶", callback_data="week:1")],
            [InlineKeyboardButton("Tag ◀", callback_data="day:-1"),
             InlineKeyboardButton("Heute", callback_data="day:0"),
             InlineKeyboardButton("Tag ▶", callback_data="day:1")],
            *rows])

    async def _send_week(self, query_or_message, chat_id: int, offset: int):
        first = _monday(cal.now().date()) + cal.timedelta(weeks=offset)
        png = await asyncio.to_thread(
            render.render_week_png, self.agent.store, first, self._people_of(chat_id))
        keyboard = self._nav_keyboard(chat_id)
        if hasattr(query_or_message, "edit_message_media"):
            try:
                await query_or_message.edit_message_media(
                    InputMediaPhoto(png), reply_markup=keyboard)
                return
            except Exception:  # message deleted/old -> fall through to a new one
                pass
        msg = await query_or_message.reply_photo(png, reply_markup=keyboard)
        old = self.widget_msg.get(chat_id)
        if old and old != msg.message_id:  # keep the chat uncluttered: one widget
            try:
                await msg.get_bot().delete_message(chat_id, old)
            except Exception:
                pass
        self.widget_msg[chat_id] = msg.message_id

    # ----------------------------------------------------------- commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            if self.owner != 0:
                return  # plan §19: no agent execution for unknown chats
            self._pair_owner(chat)  # erstes /start = Owner-Pairing
            await update.message.reply_text(
                f"Owner registriert (Chat {chat}).")
        await update.message.reply_text(
            "Kalender-Agent lokal auf dem Raspberry Pi. Schreib mir einfach:\n"
            "'Trag morgen 14 Uhr Zahnarzt ein.' · 'Was hatte ich am 7.9.?'\n"
            "'Wann können Lisa und ich nächste Woche 90 Minuten?'\n"
            "Befehle: /week · /today · /debug (nur Owner)")

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            return
        dropped = self.agent.pending.pop(str(chat), None)
        await update.message.reply_text(
            "Offene Rückfrage verworfen." if dropped else "Keine offene Rückfrage.")

    async def cmd_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            return
        await self._send_week(update.message, chat, self.week_offset.get(chat, 0))

    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            return
        await self._send_today_media(update.message, chat,
                                     self.day_offset.get(chat, 0))

    async def _send_today_media(self, target, chat: int, offset: int):
        day = cal.now().date() + cal.timedelta(days=offset)
        png = await asyncio.to_thread(
            render.render_day_png, self.agent.store, day, self._people_of(chat))
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀", callback_data="day:-1"),
             InlineKeyboardButton("Heute", callback_data="day:0"),
             InlineKeyboardButton("▶", callback_data="day:1")]])
        if hasattr(target, "edit_message_media"):
            await target.edit_message_media(
                InputMediaPhoto(png), reply_markup=keyboard)
        else:
            await target.reply_photo(png, reply_markup=keyboard)

    async def cmd_debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if chat != self.owner:  # plan §20: /debug is owner-only
            return
        if not _TRACES.exists():
            await update.message.reply_text("Noch kein Trace vorhanden.")
            return
        lines = _TRACES.read_text(encoding="utf-8").strip().splitlines()
        t = json.loads(lines[-1])
        steps = " > ".join(s["name"] for s in t.get("steps") or [])
        await update.message.reply_text(self._plain(
            f"letzter Request: {t.get('input')}\n"
            f"mode: {t.get('mode')} executed: {t.get('executed')} "
            f"conf: {t.get('confidence')}\n"
            f"steps: {steps}\nresult: {(t.get('result') or '')[:300]}"))

    # -------------------------------------------------------- message flow
    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            return  # plan §19: unknown chats never reach the agent
        text = (update.message.text or "").strip()
        if not text:
            return
        await update.message.chat.send_action("typing")
        trace = await self._run_agent(text, session_id=str(chat))
        result = self._plain(trace.get("result") or "…")
        tools = self._executed_tools(trace)
        await self._refresh_widget(update, context, chat, tools)
        # Phase 27 + §6: widget choice from the structured trace; the renderer
        # displays exactly the executed solver result (never re-schedules)
        if "calendar_find_slot" in tools:
            resolved = self._resolved(trace, "calendar_find_slot")
            day = self._day_of(trace, "calendar_find_slot")
            png = await asyncio.to_thread(
                render.render_availability_png, self.agent.store,
                resolved.get("persons", []), day, resolved)
            await update.message.reply_photo(png, caption=result[:1000])
            return
        await update.message.reply_text(result[:4000] or "…")
        if "calendar_list" in tools:
            first = self._list_first_day(trace)
            if first:
                png = await asyncio.to_thread(
                    render.render_week_png, self.agent.store, first,
                    self._people_of(chat))
                await update.message.reply_photo(png)

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        chat = query.message.chat_id
        try:  # answer immediately; stale queries raise BadRequest -> ignore
            await query.answer()
        except Exception:
            pass
        if not self._allowed_chat(chat):
            return
        kind, _, value = (query.data or "").partition(":")
        step = int(value) if value.lstrip("-").isdigit() else 0
        if kind == "week":
            self.week_offset[chat] = (self.week_offset.get(chat, 0) + step) if step else 0
            await self._send_week(query, chat, self.week_offset[chat])
        elif kind == "day":
            self.day_offset[chat] = (self.day_offset.get(chat, 0) + step) \
                if step else 0
            await self._send_today_media(query, chat, self.day_offset[chat])
        elif kind == "people":
            self.people[chat] = ["all"] if value == "all" else [value]
            await self._send_week(query, chat, self.week_offset.get(chat, 0))

    async def _send_today(self, query, chat_id: int, offset: int):
        day = cal.now().date() + cal.timedelta(days=offset)
        events = self.agent.store.events_between(
            cal.datetime.combine(day, cal.time(0, 0)),
            cal.datetime.combine(day, cal.time(0, 0)) + cal.timedelta(days=1))
        people = self._people_of(chat_id)
        if people and "all" not in people:
            events = [e for e in events if set(e.participants) & set(people)]
        text = self._plain(cal.render_events(events)) or "Keine Termine."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀", callback_data=f"day:-1"),
             InlineKeyboardButton("Heute", callback_data="day:0"),
             InlineKeyboardButton("▶", callback_data="day:1")]])
        try:
            await query.edit_message_text(text, reply_markup=keyboard)
        except Exception:
            await query.message.reply_text(text, reply_markup=keyboard)

    async def _refresh_widget(self, update, context, chat: int, tools: set[str]):
        """Keep the standing week widget in sync after create/move/delete —
        otherwise a photo rendered 30 s ago hides the new event."""
        if not tools & {"calendar_create", "calendar_move", "calendar_delete"}:
            return
        mid = self.widget_msg.get(chat)
        if not mid:
            return
        offset = self.week_offset.get(chat, 0)
        first = _monday(cal.now().date()) + cal.timedelta(weeks=offset)
        png = await asyncio.to_thread(
            render.render_week_png, self.agent.store, first, self._people_of(chat))
        try:
            await context.bot.edit_message_media(
                chat_id=chat, message_id=mid, media=InputMediaPhoto(png),
                reply_markup=self._nav_keyboard(chat))
        except Exception:
            pass  # widget was deleted or too old; a fresh /week rebuilds it

    # --------------------------------------------------------- widget data
    @staticmethod
    def _day_of(trace: dict, tool: str) -> cal.date | None:
        day = TelegramBot._resolved(trace, tool).get("day")
        try:
            return cal.date.fromisoformat(day) if day else None
        except ValueError:
            return None

    @staticmethod
    def _slot_people(trace: dict) -> list[str]:
        return TelegramBot._resolved(trace, "calendar_find_slot").get("persons") \
            or ["Ich"]

    @staticmethod
    def _list_first_day(trace: dict) -> cal.date | None:
        day = TelegramBot._resolved(trace, "calendar_list").get("first_day") or ""
        try:
            return cal.date.fromisoformat(day) if day else None
        except ValueError:
            return None


def main() -> None:
    import argparse
    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN fehlt (.env) — kein Start, kein Download.")
    owner = int(os.environ.get("TELEGRAM_OWNER_CHAT_ID", "0") or 0)
    allowed = {int(x) for x in
               os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x.strip()}
    ap = argparse.ArgumentParser(prog="local-calendar-telegram")
    ap.add_argument("--mode", choices=["needle", "hybrid"], default="hybrid")
    ap.add_argument("--db", default=os.environ.get("CALENDAR_DB", "data/calendar.db"))
    args = ap.parse_args()

    bot = TelegramBot(Agent(CalendarStore(args.db), mode=args.mode), owner, allowed)
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", bot.cmd_start))
    app.add_handler(CommandHandler("cancel", bot.cmd_cancel))
    app.add_handler(CommandHandler("week", bot.cmd_week))
    app.add_handler(CommandHandler("today", bot.cmd_today))
    app.add_handler(CommandHandler("debug", bot.cmd_debug))
    app.add_handler(CallbackQueryHandler(bot.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.on_message))
    print(f"  Telegram polling aktiv (mode={args.mode}, owner={owner}, "
          f"allowlist={len(allowed)}) — Gradio/Telegram teilen dieselbe SQLite-DB.")
    if owner == 0:
        print("  Kein Owner registriert — schreibe /start in den Bot-Chat "
              "(pairt dich automatisch als Owner).")
    app.run_polling()


if __name__ == "__main__":
    main()
