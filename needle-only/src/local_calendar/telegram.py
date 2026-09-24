"""Telegram adapter for Kalender-Pin 📌 (V1, plan §2/§3/§9/§12/§15/§28/§34).

Thin transport: Telegram Update -> RequestContext -> AtomicService ->
Response/View/Proposal -> Telegram rendering. No calendar logic lives here.
Security (plan §8): user id and chat id are separate; groups need chat AND
sender allowed plus an explicit address (mention/reply); unknown actors never
reach the model. The bot username is read from Telegram (getMe) — never
hardcoded, so a rename to @kalender_pin_bot needs no code change.

Reads are rendered from the SAME structured ReadResult that produced the text
(plan §10): no second, differently-scoped DB query.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from . import calendar as cal
from . import render, temporal, views
from .calendar import CalendarStore
from .identity import AuthConfig, resolve_group, resolve_private
from .service import AtomicService, preview_lines

_ENV_PATH: Path | None = None
READ_TOOLS = {"calendar_list", "calendar_find_slot"}
EXPECTED_N2_FT_SHA_PREFIX = "ba3212ab"   # n2-FT seed44 (HF autmoate/cactus-needle2-calendar)


def _verify_weights(path: str) -> None:
    """Production must run the N2-FT, not silently the base model (plan §1)."""
    try:
        h = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise SystemExit(f"NEEDLE_WEIGHTS nicht lesbar ({path}): {exc}")
    if not h.startswith(EXPECTED_N2_FT_SHA_PREFIX):
        print(f"  ⚠️  WARNUNG: Weights-SHA {h[:8]} erwartet "
              f"{EXPECTED_N2_FT_SHA_PREFIX} (n2-FT seed44) — falsches Modell?")


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


def _git_sha() -> str:
    """Best-effort build id for /status (plan §28). Never raises."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=Path(__file__).resolve().parents[3],
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _weights_label(weights: str | None) -> str:
    if not weights:
        return "base"
    name = Path(weights).name
    tag = "n2-ft" if "seed44" in name or "n2" in name.lower() else "custom"
    try:
        h = hashlib.sha256(Path(weights).read_bytes()).hexdigest()[:8]
        return f"{tag} ({name}, {h})"
    except OSError:
        return f"{tag} ({name})"


def _events_from_data(data: list[dict]):
    return [cal.CalendarEvent(id=d.get("id"), title=d.get("title", ""),
                              kind=d.get("kind", "appointment"),
                              start=cal.datetime.fromisoformat(d["start"]),
                              end=cal.datetime.fromisoformat(d["end"]),
                              all_day=bool(d.get("all_day")),
                              participants=list(d.get("participants") or []),
                              calendar_id=d.get("calendar_id"))
            for d in (data or [])]


class KalenderPinBot:
    def __init__(self, service: AtomicService, store: CalendarStore, auth: AuthConfig,
                 mode: str = "needle", weights: str | None = None):
        self.service = service
        self.store = store
        self.auth = auth
        self.mode = mode
        self.weights = weights
        self.username = ""          # set from getMe at startup
        self.bot_id: int | None = None
        self.started = time.time()
        self.pending_move: dict[tuple[int, int], int] = {}  # (chat, actor) -> id

    # -------------------------------------------- id-based actions (plan §4)
    def _share_group_id(self, ctx) -> int | None:
        if ctx.is_group:
            return ctx.target_calendar_id
        groups = self.store.groups_of_person(ctx.actor_person_id)
        return groups[0] if len(groups) == 1 else None

    @staticmethod
    def _short(title: str) -> str:
        return title if len(title) <= 16 else title[:15] + "…"

    def _action_keyboard(self, editable: list[tuple[int, str]], ctx):
        share_gid = self._share_group_id(ctx)
        rows = []
        for eid, title in editable[:8]:
            row = [InlineKeyboardButton(f"🗑 {self._short(title)}",
                                        callback_data=f"evd:{eid}"),
                   InlineKeyboardButton(f"↔ {self._short(title)}",
                                        callback_data=f"evm:{eid}")]
            if share_gid is not None:
                row.append(InlineKeyboardButton("👥", callback_data=f"evs:{eid}"))
            rows.append(row)
        return InlineKeyboardMarkup(rows) if rows else None

    @staticmethod
    def _editable_from_view(view) -> list[tuple[int, str]]:
        seen, out = set(), []
        for lane in getattr(view, "lanes", []):
            for b in lane.blocks:
                if b.editable and b.event_id and b.title and b.event_id not in seen:
                    seen.add(b.event_id)
                    out.append((b.event_id, b.title))
        for c in getattr(view, "shared", []):
            if c.event_id and c.event_id not in seen:
                seen.add(c.event_id)
                out.append((c.event_id, c.title))
        return out

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
        is_owner = (user.id == self.auth.owner_user_id) or \
            (self.auth.owner_user_id is None and chat.id == self.auth.owner_chat_id)
        private = chat.type == "private"
        if private:
            return resolve_private(self.store, user.id, chat.id,
                                   user.full_name or str(user.id), owner=is_owner)
        return resolve_group(self.store, chat.id, user.id,
                             user.full_name or str(user.id),
                             chat.title or "Gruppe", owner=is_owner)

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
        # a UI-initiated move waits for the new date/time (id known, no NLP)
        key = (ctx.chat_id, ctx.actor_person_id)
        eid = self.pending_move.pop(key, None)
        if eid is not None and text:
            if self._parse_when(text):
                decision = await asyncio.to_thread(
                    self.service.prepare_move_event, eid, ctx, text)
                await self._render_decision(msg, ctx, decision, text)
            else:
                self.pending_move[key] = eid
                await msg.reply_text("📌 Kein Datum/keine Uhrzeit erkannt — "
                                     "z. B. „29.9. 16:00“.")
            return
        await msg.chat.send_action("typing")
        decision = await asyncio.to_thread(self.service.prepare, text, ctx)
        await self._render_decision(msg, ctx, decision, text)

    @staticmethod
    def _parse_when(text: str) -> bool:
        return bool(cal.extract_date_from_text(text, roll=False)
                    or cal.extract_time_from_text(text)
                    or temporal.parse_nav_date(text))

    async def _render_decision(self, msg, ctx, decision, text=""):
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
        # Failed read (plan §16): short German message, NO misleading widget.
        # If the text named a date, offer a deterministic day button (no model).
        day = cal.extract_date_from_text(text, roll=False) if text else None
        kb = None
        if decision.tool in READ_TOOLS and day is not None:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"📅 {day:%d.%m.} anzeigen",
                                     callback_data=f"day:{day:%Y-%m-%d}")]])
        await msg.reply_text(decision.message or "…", reply_markup=kb)

    async def _render_read(self, msg, ctx, decision):
        await msg.reply_text(decision.message[:4000] or "…")
        read = decision.read or {}
        resolved = read.get("resolved", {})
        if decision.tool == "calendar_list":
            events = _events_from_data(read.get("data"))
            try:
                first = cal.date.fromisoformat(resolved["first_day"])
                last = cal.date.fromisoformat(resolved["last_day"])
            except (KeyError, ValueError):
                return
            if first == last:
                view = views.build_day_view_from_events(self.store, ctx, first, events)
                png = render.render_day_view(view)
            elif (last - first).days <= 6:
                view = views.build_range_view_from_events(self.store, ctx, first,
                                                          last, events)
                png = render.render_week_view(view)
            else:
                return  # > 7 days: text only, no misleading compact view (plan §13)
            kb = self._action_keyboard(self._editable_from_view(view), ctx)
            await msg.reply_photo(png, reply_markup=kb)
        elif decision.tool == "calendar_find_slot":
            if resolved.get("slots") is None:
                return  # solver produced no result -> never render a widget
            png = render.render_availability_png(
                self.store, resolved.get("persons", []), None, resolved)
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
        elif action == "day":
            try:
                day = cal.date.fromisoformat(token)
            except ValueError:
                return
            await self._send_view(query.message, ctx, day, week=False)
        elif action == "evd":  # delete an event by id (no re-identification)
            try:
                decision = await asyncio.to_thread(
                    self.service.prepare_delete_event, int(token), ctx)
            except ValueError:
                return
            await self._render_decision(query.message, ctx, decision)
        elif action == "evm":  # start an id-based move; ask for the new time
            try:
                eid = int(token)
            except ValueError:
                return
            self.pending_move[(ctx.chat_id, ctx.actor_person_id)] = eid
            await query.message.reply_text(
                "📌 Wohin verschieben? Antworte mit Datum/Uhrzeit, z. B. „29.9. 16:00“.")
        elif action == "evs":  # share/unshare to the group (visibility only)
            try:
                out = await asyncio.to_thread(
                    self.service.toggle_share, int(token), ctx,
                    self._share_group_id(ctx))
            except ValueError:
                return
            await query.message.reply_text(out.get("message", "…"))

    # ------------------------------------------------------------- commands
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        await update.message.reply_text(
            "Kalender-Pin 📌\n"
            "Eine Kalenderaktion pro Nachricht, freie natürliche Sprache.\n"
            "• Reads sofort · Änderungen erst nach Bestätigung.\n"
            "• /today · /day [Datum] · /week [Datum] · /cancel\n"
            "• In Gruppen nur bei direkter Ansprache (@… oder Reply).")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.cmd_start(update, context)

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        self.pending_move.pop((ctx.chat_id, ctx.actor_person_id), None)
        await update.message.reply_text("📌 Offene Vorschläge verfallen automatisch "
                                        "(5 Minuten) oder per ✖ Abbrechen.")

    async def _send_view(self, target, ctx, day: cal.date, week: bool):
        if week:
            first = day - cal.timedelta(days=day.weekday())
            view = await asyncio.to_thread(views.build_week_view, self.store, ctx, first)
            png = render.render_week_view(view)
        else:
            view = await asyncio.to_thread(views.build_day_view, self.store, ctx, day)
            png = render.render_day_view(view)
        kb = self._action_keyboard(self._editable_from_view(view), ctx)
        await target.reply_photo(png, reply_markup=kb)

    def _nav_day(self, context) -> cal.date:
        arg = " ".join(context.args or []).strip()
        d = temporal.parse_nav_date(arg, cal.now().date())
        return d or cal.now().date()

    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        await self._send_view(update.message, ctx, cal.now().date(), week=False)

    async def cmd_day(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        await self._send_view(update.message, ctx, self._nav_day(context), week=False)

    async def cmd_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = await asyncio.to_thread(self._context, update)
        if ctx is None:
            return
        await self._send_view(update.message, ctx, self._nav_day(context), week=True)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or user.id != self.auth.owner_user_id:
            return  # owner-only (plan §28)
        up = int(time.time() - self.started)
        await update.message.reply_text(
            "Kalender-Pin 📌\n"
            f"build: {_git_sha()}\n"
            f"mode: {self.mode}\n"
            f"model: {_weights_label(self.weights)}\n"
            f"schema: {self.store.SCHEMA_VERSION}\n"
            f"events: {self.store.event_count()}\n"
            f"uptime: {up // 3600}h {up % 3600 // 60}m\n"
            "service: ready")

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
    ap.add_argument("--allow-base", action="store_true",
                    help="DEBUG only: run without N2-FT (never in production)")
    args = ap.parse_args()

    if not args.weights and not args.allow_base:
        raise SystemExit(
            "NEEDLE_WEIGHTS fehlt — Produktion läuft ausschließlich mit N2-FT "
            "(seed44). Setze NEEDLE_WEIGHTS oder starte bewusst mit --allow-base.")
    if args.weights:
        _verify_weights(args.weights)

    store = CalendarStore(args.db)
    service = AtomicService(store, weights=args.weights)
    bot = KalenderPinBot(service, store, _cfg_from_env(), mode=args.mode,
                         weights=args.weights)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", bot.cmd_start))
    app.add_handler(CommandHandler("help", bot.cmd_help))
    app.add_handler(CommandHandler("today", bot.cmd_today))
    app.add_handler(CommandHandler("day", bot.cmd_day))
    app.add_handler(CommandHandler("week", bot.cmd_week))
    app.add_handler(CommandHandler("status", bot.cmd_status))
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
