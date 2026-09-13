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


def _load_env() -> None:
    """Reuse the existing workspace .env (needle-only/.env, then root .env)."""
    for candidate in (Path(__file__).resolve().parents[2] / ".env",
                      Path(__file__).resolve().parents[3] / ".env"):
        if candidate.exists():
            load_dotenv(candidate)


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

    # ----------------------------------------------------------- security
    def _allowed_chat(self, chat_id: int) -> bool:
        return chat_id == self.owner or chat_id in self.allowed

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _plain(text: str | None) -> str:
        return (text or "").replace("**", "")

    async def _run_agent(self, text: str) -> dict:
        def work():
            final = None
            for trace in self.agent.handle(text):
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
        people = [InlineKeyboardButton("Alle", callback_data="people:all"),
                  InlineKeyboardButton("Ich", callback_data="people:Ich")]
        people += [InlineKeyboardButton(p, callback_data=f"people:{p}")
                   for p in self.agent.store.participant_names() if p != "Ich"]
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("◀ Woche", callback_data="week:-1"),
             InlineKeyboardButton("Heute", callback_data="week:0"),
             InlineKeyboardButton("Woche ▶", callback_data="week:1")],
            [InlineKeyboardButton("Tag ◀", callback_data="day:-1"),
             InlineKeyboardButton("Heute", callback_data="day:0"),
             InlineKeyboardButton("Tag ▶", callback_data="day:1")],
            people])

    async def _send_week(self, query_or_message, chat_id: int, offset: int):
        first = _monday(cal.now().date()) + cal.timedelta(weeks=offset)
        png = await asyncio.to_thread(
            render.render_week_png, self.agent.store, first, self._people_of(chat_id))
        keyboard = self._nav_keyboard(chat_id)
        if hasattr(query_or_message, "edit_message_media"):
            await query_or_message.edit_message_media(
                InputMediaPhoto(png), reply_markup=keyboard)
        else:
            await query_or_message.reply_photo(png, reply_markup=keyboard)

    # ----------------------------------------------------------- commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            return  # plan §19: no agent execution for unknown chats
        await update.message.reply_text(
            "Kalender-Agent lokal auf dem Raspberry Pi. Schreib mir einfach:\n"
            "'Trag morgen 14 Uhr Zahnarzt ein.' · 'Was hatte ich am 7.9.?'\n"
            "'Wann können Lisa und ich nächste Woche 90 Minuten?'\n"
            "Befehle: /week · /today · /debug (nur Owner)")

    async def cmd_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            return
        await self._send_week(update.message, chat, self.week_offset.get(chat, 0))

    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat.id
        if not self._allowed_chat(chat):
            return
        offset = self.day_offset.get(chat, 0)
        day = cal.now().date() + cal.timedelta(days=offset)
        events = self.agent.store.events_between(
            cal.datetime.combine(day, cal.time(0, 0)),
            cal.datetime.combine(day, cal.time(0, 0)) + cal.timedelta(days=1))
        people = self._people_of(chat)
        if people and "all" not in people:
            events = [e for e in events if set(e.participants) & set(people)]
        await update.message.reply_text(
            self._plain(cal.render_events(events)) or "Keine Termine an diesem Tag.")

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
        trace = await self._run_agent(text)
        result = self._plain(trace.get("result") or "…")
        tools = self._executed_tools(trace)
        # Phase 27: widget choice from the structured trace, never from text
        if "calendar_find_slot" in tools:
            day = self._day_of(trace, "calendar_find_slot")
            png = await asyncio.to_thread(
                render.render_availability_png, self.agent.store,
                self._slot_people(trace), day)
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
        if not self._allowed_chat(chat):
            await query.answer("Kein Zugriff.")
            return
        kind, _, value = (query.data or "").partition(":")
        step = int(value) if value.lstrip("-").isdigit() else 0
        if kind == "week":
            self.week_offset[chat] = (self.week_offset.get(chat, 0) + step) if step else 0
            await self._send_week(query, chat, self.week_offset[chat])
        elif kind == "day":
            self.day_offset[chat] = (self.day_offset.get(chat, 0) + step) \
                if step else 0
            await self._send_today(query, chat, self.day_offset[chat])
        elif kind == "people":
            self.people[chat] = ["all"] if value == "all" else [value]
            await self._send_week(query, chat, self.week_offset.get(chat, 0))
        await query.answer()

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
    app.add_handler(CommandHandler("week", bot.cmd_week))
    app.add_handler(CommandHandler("today", bot.cmd_today))
    app.add_handler(CommandHandler("debug", bot.cmd_debug))
    app.add_handler(CallbackQueryHandler(bot.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.on_message))
    print(f"  Telegram polling aktiv (mode={args.mode}, owner={owner}, "
          f"allowlist={len(allowed)}) — Gradio/Telegram teilen dieselbe SQLite-DB.")
    app.run_polling()


if __name__ == "__main__":
    main()
