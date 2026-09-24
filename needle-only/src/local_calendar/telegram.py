"""Telegram adapter for Kalender-Pin 📌 (V1, plan §2/§3/§9/§12/§21/§24).

Thin transport: Telegram Update -> RequestContext -> AtomicService ->
Response/View/Proposal -> Telegram rendering. No calendar logic lives here.
Security (plan §8): user id and chat id are separate; groups need chat AND
sender allowed plus an explicit address (mention/reply); unknown actors never
reach the model. The bot username is read from Telegram (getMe) — never
hardcoded, so a rename to @kalender_pin_bot needs no code change.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
                      Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from . import calendar as cal
from . import render, views
from .calendar import CalendarStore
from .identity import AuthConfig, resolve_group, resolve_private
from .service import AtomicService, preview_lines

_ENV_PATH: Path | None = None


def _load_env() -> Path | None:
    global _ENV_PATH
    for candidate in (Path(__file__).resolve().parents[2] / ".env",
                      Path(__file__).resolve().parents[3] / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            _ENV_PATH = candidate
            return candidate
    return None


def _cfg_from_env() -> AuthConfig:
    def _ids(key: str) -> set[int]:
        return {int(x) for x in os.environ.get(key, "").split(",") if x.strip()}
    owner_user = os.environ.get("TELEGRAM_OWNER_USER_ID", "").strip()
    return AuthConfig(
        owner_user_id=int(owner_user) if owner_user else None,
        allowed_user_ids=_ids("TELEGRAM_ALLOWED_USER_IDS"),
        allowed_chat_ids=_ids("TELEGRAM_ALLOWED_CHAT_IDS"),
        owner_chat_id=int(os.environ["TELEGRAM_OWNER_CHAT_ID"])
        if os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip() else None,
        allow_first_start_pairing=os.environ.get(
            "TELEGRAM_ALLOW_FIRST_START_PAIRING", "0") == "1",
    )


class KalenderPinBot:
    def __init__(self, service: AtomicService, store: CalendarStore, auth: AuthConfig):
        self.service = service
        self.store = store
        self.auth = auth
        self.username = ""          # set from getMe at startup
        self.bot_id: int | None = None
        self.week_offset: dict[int, int] = {}
        self.day_offset: dict[int, int] = {}

    # ----------------------------------------------------- addressing (§9)
    def _stripped_mention(self, message) -> tuple[bool, str]:
        """True + cleaned text when the bot is addressed via a Telegram
        mention/bot_command entity (not a hand-rolled regex, plan §2)."""
        text = message.text or ""
        for e in (message.entities or []):
            at = text[e.offset:e.offset + e.length]
            if e.type == "mention" and at.lstrip("@").lower() == self.username.lower():
                return True, (text[:e.offset] + text[e.offset + e.length:]).strip()
            if e.type == "bot_command" and "@" in at \
                    and at.split("@", 1)[1].lower() == self.username.lower():
                return True, (text[:e.offset] + "@" + at.split("@", 1)[0].lstrip("/")
                              + text[e.offset + e.length:]).strip()
        return False, text

    def _is_reply_to_bot(self, message) -> bool:
        r = message.reply_to_message
        return bool(r and r.from_user and r.from_user.id == self.bot_id)

    def _context(self, update: Update):
        """Authorized RequestContext or None (unknown actors never run)."""
        chat, user = update.effective_chat, update.effective_user
        if chat is None or user is None:
            return None
        if not self.auth.is_authorized(user.id, chat.id, chat.type):
            return None
        private = chat.type == "private"
        if private:
            return resolve_private(self.store, user.id, chat.id,
                                   user.full_name or str(user.id))
        return resolve_group(self.store, chat.id, user.id,
                             user.full_name or str(user.id),
                             chat.title or "Gruppe")

    # --------------------------------------------------------- message flow
    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = update.message
        if msg is None or not (msg.text or "").strip():
            return
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return  # unauthorized: no inference, no DB, no reply (plan §8)
        text = msg.text.strip()
        if ctx.is_group:
            mentioned, text = self._stripped_mention(msg)
            if not (mentioned or self._is_reply_to_bot(msg)):
                return  # group chatter is ignored before any inference (§9)
            if not text:
                await msg.reply_text("📌 Ja? Schreib mir deine Kalenderanfrage.")
                return
        await msg.chat.send_action("typing")
        decision = await asyncio.to_thread(self.service.prepare, text, ctx)
        await self._render_decision(msg, ctx, decision)

    async def _render_decision(self, msg, ctx, decision):
        if decision.kind == "read":
            await self._render_read(msg, ctx, decision)
            return
        if decision.kind == "write_proposal":
            lines = preview_lines(decision, self.store, ctx)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Bestätigen",
                                     callback_data=f"cfm:{decision.proposal['token']}"),
                InlineKeyboardButton("✖ Abbrechen",
                                     callback_data=f"cnl:{decision.proposal['token']}")]])
            await msg.reply_text("\n".join(lines), reply_markup=kb)
            return
        await msg.reply_text(decision.message or "…")

    async def _render_read(self, msg, ctx, decision):
        await msg.reply_text(decision.message[:4000] or "…")
        if decision.tool == "calendar_list":
            first = decision.read.get("resolved", {}).get("first_day")
            try:
                day = cal.date.fromisoformat(first) if first else cal.now().date()
            except ValueError:
                day = cal.now().date()
            monday = day - cal.timedelta(days=day.weekday())
            view = await asyncio.to_thread(views.build_week_view, self.store, ctx, monday)
            await msg.reply_photo(render.render_week_view(view))
        elif decision.tool == "calendar_find_slot":
            resolved = decision.read.get("resolved", {})
            day = cal.now().date()
            png = await asyncio.to_thread(
                render.render_availability_png, self.store,
                resolved.get("persons", []), day, resolved)
            await msg.reply_photo(png)

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        action, _, token = (query.data or "").partition(":")
        if action == "cfm":
            out = await asyncio.to_thread(self.service.confirm, token, ctx)
            await query.edit_message_text(out.get("message", "…"))
        elif action == "cnl":
            await asyncio.to_thread(self.service.cancel, token, ctx)
            await query.edit_message_text("📌 Abgebrochen — keine Änderung.")

    # ------------------------------------------------------------- commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        await update.message.reply_text(
            "Kalender-Pin 📌\n"
            "Eine Kalenderaktion pro Nachricht, freie natürliche Sprache.\n"
            "• Reads sofort · Änderungen erst nach Bestätigung.\n"
            "• /today · /week · /cancel\n"
            "• In Gruppen nur bei direkter Ansprache (@… oder Reply).")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.cmd_start(update, context)

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await asyncio.to_thread(self._context, update) is None:
            return
        await update.message.reply_text("📌 Offene Vorschläge verfallen automatisch "
                                        "(5 Minuten) oder per ✖ Abbrechen.")

    async def _send_view(self, target, ctx, day: cal.date, week: bool):
        if week:
            first = day - cal.timedelta(days=day.weekday())
            view = await asyncio.to_thread(views.build_week_view, self.store, ctx, first)
            await target.reply_photo(render.render_week_view(view))
        else:
            view = await asyncio.to_thread(views.build_day_view, self.store, ctx, day)
            await target.reply_photo(render.render_day_view(view))

    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        await self._send_view(update.message, ctx, cal.now().date(), week=False)

    async def cmd_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        await self._send_view(update.message, ctx, cal.now().date(), week=True)

    async def cmd_debug(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or user.id != self.auth.owner_user_id:
            return  # owner-only (plan §3)
        await update.message.reply_text(
            "📌 debug: Needle-only, ein Vorschlag pro Nachricht, Scope aktiv.")


def main() -> None:
    import argparse
    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN fehlt (.env) — kein Start.")
    ap = argparse.ArgumentParser(prog="local-calendar-telegram")
    ap.add_argument("--mode", choices=["needle", "hybrid"], default="needle")
    ap.add_argument("--db", default=os.environ.get("CALENDAR_DB", "data/calendar.db"))
    ap.add_argument("--weights", default=os.environ.get("NEEDLE_WEIGHTS") or None)
    args = ap.parse_args()

    store = CalendarStore(args.db)
    service = AtomicService(store, weights=args.weights)
    bot = KalenderPinBot(service, store, _cfg_from_env())

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", bot.cmd_start))
    app.add_handler(CommandHandler("help", bot.cmd_help))
    app.add_handler(CommandHandler("today", bot.cmd_today))
    app.add_handler(CommandHandler("week", bot.cmd_week))
    app.add_handler(CommandHandler("cancel", bot.cmd_cancel))
    app.add_handler(CommandHandler("debug", bot.cmd_debug))
    app.add_handler(CallbackQueryHandler(bot.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.on_message))

    async def _post_init(application):
        me = await application.bot.get_me()  # username from Telegram (§2)
        bot.username, bot.bot_id = me.username or "", me.id
        print(f"  Kalender-Pin 📌 aktiv (@{bot.username}) — Needle-only, "
              f"weights={'custom' if args.weights else 'base'}.")
        application.create_task(_daily_backup(store))

    async def _daily_backup(store):
        """Daily SQLite backup (plan §33); failures are swallowed, never crash."""
        while True:
            store.backup()
            await asyncio.sleep(24 * 3600)

    app.post_init = _post_init
    app.run_polling()


if __name__ == "__main__":
    main()
