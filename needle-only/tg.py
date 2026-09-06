"""Telegram-Bot „needle 📌" v5.3: CRUD-basierte Kalender- und Notizverwaltung.
7 Tools: calendar_create/edit/read/delete + note_write/read/delete.
Hard-Deletes. Erinnerungen werden nach Feuern gelöscht.
Start: uv run python needle-only/tg.py"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup  # noqa: E402
from telegram.ext import (Application, CommandHandler, MessageHandler,  # noqa: E402
                          CallbackQueryHandler, ContextTypes, filters)

from modules.timesync import now as tz_now  # noqa: E402
from run import WRITE, build, draft_calls  # noqa: E402

BOT_NAME = "needle 📌"

# Pending Approvals: chat_id -> {"plan": ..., "message_id": ...}
_pending: dict[int, dict] = {}

# Globals (werden in main() gesetzt)
tools = None
agent = None
fns = None


def _is_owner(update: Update) -> bool:
    chat_id = update.effective_chat.id
    owner_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    return str(chat_id) == owner_id if owner_id else False


# ---------- Commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registriert den Chat als Owner (falls TELEGRAM_OWNER_CHAT_ID leer)."""
    chat_id = update.effective_chat.id
    owner_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()

    if not owner_id:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        # Bestehende (leere) Zeile ersetzen ODER anhängen
        lines = env_path.read_text().splitlines()
        found = False
        for i, line in enumerate(lines):
            if line.startswith("TELEGRAM_OWNER_CHAT_ID="):
                lines[i] = f"TELEGRAM_OWNER_CHAT_ID={chat_id}"
                found = True
                break
        if not found:
            lines.append(f"TELEGRAM_OWNER_CHAT_ID={chat_id}")
        env_path.write_text("\n".join(lines) + "\n")
        os.environ["TELEGRAM_OWNER_CHAT_ID"] = str(chat_id)
        await update.message.reply_text(
            f"{BOT_NAME}\n\n✅ Du bist jetzt der Owner (chat_id={chat_id}).\n\n"
            f"Beispiele:\n"
            f"  erstelle einen termin zahnarzt am 10.9. 10 uhr\n"
            f"  erinnere mich in 10 min an wasser\n"
            f"  was steht diese woche an?\n\n"
            f"  /status — Zähler\n  /help — Hilfe")
        return

    if str(chat_id) != owner_id:
        await update.message.reply_text(f"{BOT_NAME} ❌ Nicht autorisiert.")
        return

    await update.message.reply_text(
        f"{BOT_NAME}\n\n✅ Bereits verbunden.\n\n  /status — Zähler\n  /help — Hilfe")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    result = tools.orga.calendar_read(kind="all", horizon="monat")
    await update.message.reply_text(result)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    await update.message.reply_text(
        f"{BOT_NAME} — Needle-Orga-Bot (45M, lokal)\n\n"
        f"📅 Termine:\n"
        f"  erstelle einen termin zahnarzt am 10.9. 10 uhr\n"
        f"  verschiebe zahnarzt auf 11:30\n"
        f"  lösche zahnarzt\n\n"
        f"⏰ Erinnerungen:\n"
        f"  erinnerung wasser trinken in 10 min\n"
        f"  zeige erinnerungen\n\n"
        f"📋 Aufgaben:\n"
        f"  aufgabe bericht schreiben bis freitag\n\n"
        f"📝 Notizen:\n"
        f"  merk dir feuerholz kostet 8 euro\n"
        f"  was weißt du über feuerholz?\n"
        f"  lösche notiz feuerholz\n\n"
        f"Commands: /status /help")


# ---------- Message Handling ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text → Needle-Draft → Plan/Read → Antwort (mit Inline-Approval)."""
    if not _is_owner(update):
        await update.message.reply_text(f"{BOT_NAME} ❌ Nicht autorisiert.")
        return

    text = update.message.text.strip()
    if not text:
        return

    # Needle blockiert → im Executor laufen lassen
    loop = asyncio.get_event_loop()
    calls = await loop.run_in_executor(
        None, lambda: draft_calls(agent, fns, text))

    if not calls:
        await update.message.reply_text(
            "⚠️ Das konnte ich nicht verarbeiten.\n\n"
            "💡 Siehe /help für Beispiele.")
        return

    reads, writes = [], []
    for call in calls:
        name = call["tool"]
        args = call["arguments"]
        if name in WRITE:
            writes.append(call)
        else:
            reads.append(tools.execute(name, args, text))

    if writes:
        # Plan erstellen und mit Inline-Keyboard freigeben
        plan = tools.plan(writes, text)
        if not plan["ops"]:
            await update.message.reply_text(
                "\n".join(plan["lines"]) or "⚠️ Kein Plan erzeugt.")
            return
        plan_lines = plan["lines"] + [f"⚠ {w}" for w in plan.get("warn", [])]
        keyboard = [
            [InlineKeyboardButton("✅ Ausführen", callback_data="approve"),
             InlineKeyboardButton("❌ Nein", callback_data="reject")],
            [InlineKeyboardButton("✏️ Korrektur", callback_data="edit")],
        ]
        msg = await update.message.reply_text(
            f"📋 Plan:\n\n" + "\n".join(plan_lines) + "\n\nAusführen?",
            reply_markup=InlineKeyboardMarkup(keyboard))
        _pending[update.effective_chat.id] = {
            "plan": plan, "message_id": msg.message_id}
        return

    if reads:
        await update.message.reply_text("\n".join(r for r in reads if r))
    else:
        await update.message.reply_text(
            "⚠️ Das konnte ich nicht verarbeiten.\n\n"
            "💡 Siehe /help für Beispiele.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline-Keyboard-Callback: ✅ approve / ❌ reject / ✏️ edit."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    pending = _pending.get(chat_id)
    if not pending or pending.get("message_id") != query.message.message_id:
        await query.edit_message_text("⚠️ Approval bereits verarbeitet.")
        return

    action = query.data
    plan = pending["plan"]

    if action == "approve":
        result = tools.apply(plan)
        await query.edit_message_text(f"✅ {result}")
        _pending.pop(chat_id, None)
    elif action == "reject":
        await query.edit_message_text("❌ Abgelehnt. Nichts gespeichert.")
        _pending.pop(chat_id, None)
    elif action == "edit":
        await query.edit_message_text(
            "✏️ Sende die Korrektur als neue Nachricht.")
        _pending.pop(chat_id, None)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"❌ Telegram-Fehler: {context.error}")


# ---------- Scheduler (JobQueue) ----------

async def scheduler_tick(context: ContextTypes.DEFAULT_TYPE):
    """Alle 30s: Fällige Reminder feuern (und löschen) + Appointment-Alarme."""
    now = tz_now()
    owner_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not owner_id:
        return

    # 1) Fällige Reminder feuern und SOFORT LÖSCHEN
    due = tools.orga._q(
        "SELECT id, title FROM entries "
        "WHERE kind = 'reminder' AND start_at <= %s "
        "ORDER BY start_at LIMIT 10",
        (now,))
    for rid, title in due:
        tools.orga._q("DELETE FROM entries WHERE id = %s", (rid,))
        try:
            await context.bot.send_message(
                chat_id=int(owner_id), text=f"⏰ {title}")
        except Exception as exc:
            print(f"❌ Telegram-Push fehlgeschlagen: {exc}")

    # 2) Appointment-Alarme feuern (einmalig via alarmed_at)
    alarms = tools.orga._q(
        "SELECT id, title, start_at FROM entries "
        "WHERE kind = 'appointment' AND alarm_min IS NOT NULL "
        "AND alarmed_at IS NULL "
        "AND start_at - (alarm_min || ' minutes')::interval <= %s "
        "AND start_at >= %s "
        "ORDER BY start_at LIMIT 10",
        (now, now))
    for aid, title, start_at in alarms:
        tools.orga._q(
            "UPDATE entries SET alarmed_at = now() WHERE id = %s",
            (aid,))
        remaining = max(0, int((start_at - now).total_seconds() / 60))
        try:
            await context.bot.send_message(
                chat_id=int(owner_id), text=f"🔔 {title} in {remaining} min")
        except Exception as exc:
            print(f"❌ Telegram-Push fehlgeschlagen: {exc}")


# ---------- Main ----------

def main():
    global tools, agent, fns

    from dotenv import load_dotenv
    load_dotenv(override=True)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN nicht gesetzt.")
        print("")
        print("Setup:")
        print("  1. Öffne https://t.me/BotFather")
        print("  2. /newbot → Namen wählen (z.B. 'needle bot')")
        print("     → Username wählen (z.B. 'needle_pin_bot')")
        print("  3. Kopiere den Token (Format: 123456:ABC-DEF...)")
        print("  4. Füge in .env hinzu:")
        print("     TELEGRAM_BOT_TOKEN=<dein-token>")
        print("  5. Starte erneut: uv run python needle-only/tg.py")
        print("  6. Schicke /start an deinen Bot → du wirst Owner")
        sys.exit(1)

    # Orga-Stack aufsetzen (Needle + Orga + MiniTools)
    print(f"{BOT_NAME} — Lade Needle-Engine...")
    tools, agent, fns = build()

    # Bot konfigurieren
    app = Application.builder().token(token).build()

    # Handler registrieren
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)

    # Scheduler via JobQueue (alle 30s, erster Tick nach 5s)
    if app.job_queue:
        app.job_queue.run_repeating(scheduler_tick, interval=30, first=5)

    print(f"{BOT_NAME} — Telegram-Bot läuft (Polling)...")
    print(f"  Schicke /start an deinen Bot, um dich zu registrieren.")
    print(f"  Beenden: Ctrl+C")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
