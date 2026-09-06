"""Telegram-Bot „needle 📌" v5.3: CRUD + Y/E/N-Approval via Inline-Keyboard.
7 Tools: calendar_create/edit/read/delete + note_write/read/delete.
Start: uv run python needle-only/tg.py"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(override=True)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from modules.timesync import now as tz_now
from run import WRITE, build, draft_calls

BOT_NAME = "needle 📌"

# Pending Approvals: chat_id -> {"writes": [...], "message_id": ...}
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
            f"  erstelle einen termin zahnarzt am 10.9. um 10 uhr\n"
            f"  was steht diese woche an?\n"
            f"  merk dir feuerholz kostet 8 euro\n\n"
            f"  /status — Zähler\n  /help — Hilfe")
        return

    if str(chat_id) != owner_id:
        await update.message.reply_text(f"{BOT_NAME} ❌ Nicht autorisiert.")
        return

    await update.message.reply_text(
        f"{BOT_NAME}\n\n✅ Bereits verbunden.\n\n  /status — Zähler\n  /help — Hilfe")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt Kalender- und Notiz-Übersicht."""
    if not _is_owner(update):
        return
    result = tools.orga.calendar_read(kind="all", horizon="monat")
    await update.message.reply_text(result)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt Hilfe mit Beispielen."""
    if not _is_owner(update):
        return
    await update.message.reply_text(
        f"{BOT_NAME} — Needle-Orga-Bot (45M, lokal)\n\n"
        f"📅 Termine:\n"
        f"  erstelle einen termin zahnarzt am 10.9. um 10 uhr\n"
        f"  verschiebe zahnarzt auf 11:30\n"
        f"  entferne zahnarzt aus dem kalender\n\n"
        f"⏰ Erinnerungen:\n"
        f"  stell eine erinnerung wasser trinken in 10 min\n"
        f"  zeige erinnerungen\n\n"
        f"📋 Aufgaben:\n"
        f"  erstelle eine aufgabe bericht schreiben bis freitag\n\n"
        f"📝 Notizen:\n"
        f"  merk dir feuerholz kostet 8 euro\n"
        f"  was weißt du über feuerholz?\n"
        f"  lösche die notiz feuerholz\n\n"
        f"Commands: /status /help")


# ---------- Message Handling ----------

def _get_clarification(text: str) -> str | None:
    """Returns a clarifying question for incomplete commands."""
    import re
    low = text.lower().strip()

    # Incomplete reminder/erinnerung command
    if re.search(r'erstelle\s+(eine\s+)?erinnerung\s*$', low) or \
       re.search(r'erinner\w*\s+mich\s*$', low) or \
       re.search(r'stell\s+(eine\s+)?erinnerung\s*$', low):
        return ("⏰ Woran soll ich dich erinnern?\n\n"
                "Beispiel: 'erinnerung wasser trinken in 10 minuten'")

    # Incomplete note command
    if re.search(r'merk\s+dir\s*$', low) or \
       re.search(r'erstelle\s+(eine\s+)?notiz\s*$', low) or \
       re.search(r'notiere\s*$', low) or \
       re.search(r'speicher\w*\s*$', low):
        return ("📝 Was soll ich mir merken?\n\n"
                "Beispiel: 'merk dir feuerholz kostet 8 euro'")

    # Incomplete appointment command
    if re.search(r'erstelle\s+(einen\s+)?termin\s*$', low) or \
       re.search(r'neuer\s+termin\s*$', low):
        return ("📅 Welchen Termin soll ich erstellen?\n\n"
                "Beispiel: 'erstelle einen termin zahnarzt am 10.9. um 10 uhr'")

    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Text → Clarification-Check → Needle → Y/E/N-Approval → Antwort."""
    if not _is_owner(update):
        await update.message.reply_text(f"{BOT_NAME} ❌ Nicht autorisiert.")
        return

    text = update.message.text.strip()
    if not text:
        return

    # 1) Incomplete-Command-Check VOR der Pipeline
    #    (verhindert Bad-Calls wie note_write(subject='Merk dir'))
    clarification = _get_clarification(text)
    if clarification:
        await update.message.reply_text(clarification)
        return

    # 2) Needle blockiert → im Executor laufen lassen
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

    # Writes → Y/E/N-Approval mit Inline-Keyboard
    if writes:
        plan_lines = [_format_plan_line(w) for w in writes]
        keyboard = [
            [InlineKeyboardButton("✅ Ja", callback_data="approve"),
             InlineKeyboardButton("❌ Nein", callback_data="reject")],
            [InlineKeyboardButton("✏️ Bearbeiten", callback_data="edit")],
        ]
        msg = await update.message.reply_text(
            f"📋 Plan:\n\n" + "\n".join(plan_lines) + "\n\nAusführen?",
            reply_markup=InlineKeyboardMarkup(keyboard))
        _pending[update.effective_chat.id] = {
            "writes": writes, "message_id": msg.message_id, "text": text}
        return

    # Nur Reads → direkt antworten
    if reads:
        await update.message.reply_text("\n".join(r for r in reads if r))
    else:
        await update.message.reply_text(
            "⚠️ Das konnte ich nicht verarbeiten.\n\n"
            "💡 Siehe /help für Beispiele.")


def _format_dt(dt) -> str:
    """Formatiert ein datetime-Objekt lesbar: 'Di 08.09. 09:00'."""
    if dt is None:
        return "?"
    if isinstance(dt, str):
        from orga import _norm_dt
        dt = _norm_dt(dt)
    if dt is None:
        return "?"
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][dt.weekday()]
    return f"{wd} {dt.strftime('%d.%m.')} {dt.strftime('%H:%M')}"


def _format_plan_line(w: dict) -> str:
    """Formatiert einen Write-Call für die Approval-Anzeige."""
    tool = w["tool"]
    args = w["arguments"]

    if tool == "calendar_create":
        title = args.get("title", "?")
        start = args.get("start_at", "?")
        kind = args.get("kind", "appointment")
        icons = {"appointment": "📅", "reminder": "⏰", "task": "📋"}
        icon = icons.get(kind, "📅")
        kind_labels = {"appointment": "Termin", "reminder": "Erinnerung", "task": "Aufgabe"}
        label = kind_labels.get(kind, "Termin")
        return f"{icon} {label}: {title}\n🕐 {_format_dt(start)}"

    if tool == "calendar_edit":
        title = args.get("title", "?")
        start = args.get("start_at")
        if start:
            return f"✏️ Verschiebe '{title}' auf {_format_dt(start)}"
        return f"✏️ Bearbeite '{title}'"

    if tool == "calendar_delete":
        title = args.get("title", "?")
        start = args.get("start_at")
        if start:
            return f"🗑️ Lösche '{title}' ({_format_dt(start)})"
        return f"🗑️ Lösche '{title}'"

    if tool == "note_write":
        subject = args.get("subject", "?")
        body = args.get("body", "")
        if body:
            return f"📝 Notiz: {subject}\n📄 {body}"
        return f"📝 Notiz: {subject}"

    if tool == "note_delete":
        subject = args.get("subject", "?")
        return f"🗑️ Lösche Notiz '{subject}'"

    return f"🔧 {tool}: {args}"


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline-Keyboard-Callback: ✅ Ja / ❌ Nein / ✏️ Bearbeiten."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat.id
    pending = _pending.get(chat_id)
    if not pending or pending.get("message_id") != query.message.message_id:
        await query.answer("⚠️ Bereits verarbeitet.", show_alert=True)
        return

    action = query.data
    writes = pending["writes"]

    if action == "approve":
        # Writes ausführen (im Executor, da DB blockiert)
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: [tools._execute_write(w["tool"], w["arguments"])
                           for w in writes])
        await query.edit_message_text(
            "✅ Ausgeführt:\n\n" + "\n".join(results))
        _pending.pop(chat_id, None)
    elif action == "reject":
        await query.edit_message_text(
            "❌ Abgelehnt. Nichts gespeichert.")
        _pending.pop(chat_id, None)
    elif action == "edit":
        await query.edit_message_text(
            "✏️ Sende die Korrektur als neue Nachricht.\n\n"
            f"Original: {pending['text']}")
        _pending.pop(chat_id, None)


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

    # Router-Warmup: Embedding-Modell VOR dem ersten Request laden
    # (sonst dauert der erste User-Request 10-30s wegen Lazy-Loading)
    print(f"{BOT_NAME} — Lade Embedding-Modell (Router-Warmup)...")
    from run import _get_router
    _router = _get_router()
    _router.route("warmup needleorga")  # Triggert Modell-Loading
    print(f"{BOT_NAME} — Router bereit.")

    # Bot konfigurieren
    app = Application.builder().token(token).build()

    # Handler registrieren
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Scheduler via JobQueue (alle 30s, erster Tick nach 5s)
    if app.job_queue:
        app.job_queue.run_repeating(scheduler_tick, interval=30, first=5)

    print(f"{BOT_NAME} — Telegram-Bot läuft (Polling)...")
    print(f"  Schicke /start an deinen Bot, um dich zu registrieren.")
    print(f"  Beenden: Ctrl+C")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
