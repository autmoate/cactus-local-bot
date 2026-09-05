"""Telegram-Bot „needle 📌" v5.0: Orga-Bot via python-telegram-bot v22.
Owner-only (TELEGRAM_OWNER_CHAT_ID). Approvals via Inline-Keyboard.
Scheduler (Reminder + Appointment-Alarme) läuft als JobQueue-Callback
und pusht Nachrichten direkt an den Owner.
Start: uv run python needle-only/telegram.py"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        # Erster /start wird zum Owner → in .env schreiben + os.environ setzen
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
        # WICHTIG: os.environ aktualisieren, sonst kann sich jeder als Owner registrieren
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
    await update.message.reply_text(tools.orga.status())


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/delete <titel> — Eintrag hart löschen (Needle-Bypass)."""
    if not _is_owner(update):
        return
    title = " ".join(context.args) if context.args else ""
    if not title:
        await update.message.reply_text(
            "Verwendung: /delete <titel>\nBeispiel: /delete zahnarzt")
        return
    # Suche in Terminen UND Erinnerungen
    hit = tools.orga._find_event(title) or tools.orga._find_reminder(title)
    if not hit:
        await update.message.reply_text(f"❌ '{title}' nicht gefunden.")
        return
    tools.orga._q("delete from entries where id = %s", (hit[0],))
    await update.message.reply_text(f"🗑️ Gelöscht: {hit[1]}")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/list [termine|erinnerungen|notizen] — Liste anzeigen (Needle-Bypass)."""
    if not _is_owner(update):
        return
    what = context.args[0] if context.args else "termine"
    result = tools.orga.list_entries(what, "woche")
    await update.message.reply_text(result)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return
    await update.message.reply_text(
        f"{BOT_NAME} — Needle-Orga-Bot (45M, lokal)\n\n"
        f"Termine:\n"
        f"  erstelle einen termin zahnarzt am 10.9. 10 uhr\n"
        f"  verschiebe zahnarzt auf 11:30\n"
        f"  sage zahnarzt ab\n\n"
        f"Erinnerungen:\n"
        f"  erinnere mich in 10 min an wasser\n"
        f"  wasser ist erledigt\n\n"
        f"Kalender:\n"
        f"  was steht diese woche an?\n"
        f"  wann habe ich zeit?\n\n"
        f"Notizen:\n"
        f"  merk dir: feuerholz kostet 8 euro\n"
        f"  was weißt du über feuerholz?\n\n"
        f"Commands:\n"
        f"  /delete <titel> — hart löschen\n"
        f"  /list [termine|erinnerungen] — auflisten\n"
        f"  /status — Zähler")


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
        # Needle hat nichts verstanden → hilfreiche Fehlermeldung
        await update.message.reply_text(
            f"⚠️ Konnte nichts extrahieren.\n\n"
            f"Versuche:\n"
            f"  erstelle einen termin zahnarzt am 10.9. 10 uhr\n"
            f"  sage zahnarzt ab\n"
            f"  erinnere mich in 10 min an wasser\n"
            f"  was steht diese woche an?\n"
            f"  lösche zahnarzt")
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
        # Needle hat Calls geliefert aber nichts gelesen oder geplant
        await update.message.reply_text(
            f"⚠️ Konnte nichts extrahieren. Versuche /help für Beispiele.")


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
        _pending.pop(chat_id, None)  # Neue Nachricht = neuer Plan


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"❌ Telegram-Fehler: {context.error}")


# ---------- Scheduler (JobQueue) ----------

async def scheduler_tick(context: ContextTypes.DEFAULT_TYPE):
    """Alle 30s: Fällige Reminder feuern + Appointment-Alarme."""
    orga = tools.orga
    owner_id = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    if not owner_id:
        return
    now = tz_now()

    # 1) Fällige Reminder → status='done' + Telegram-Push
    due = orga._q(
        "select id, title from entries "
        "where kind='reminder' and status='active' "
        "and start_at <= %s order by start_at limit 5",
        (now,))
    for rid, title in due:
        orga._q("update entries set status='done', updated_at=now() "
                "where id=%s", (rid,))
        try:
            await context.bot.send_message(
                chat_id=int(owner_id), text=f"⏰ Erinnerung: {title}")
        except Exception as exc:
            print(f"❌ Telegram-Push fehlgeschlagen: {exc}")

    # 2) Appointment-Alarme (einmalig via alarmed_at) → Telegram-Push
    alarms = orga._q(
        "select id, title, start_at from entries "
        "where kind='appointment' and status='active' "
        "and alarm_min is not null and alarmed_at is null "
        "and start_at - (alarm_min || ' minutes')::interval <= %s "
        "and start_at >= %s order by start_at limit 5",
        (now, now))
    for aid, title, start_at in alarms:
        orga._q("update entries set alarmed_at=now(), updated_at=now() "
                "where id=%s", (aid,))
        remaining = max(0, int((start_at - now).total_seconds() / 60))
        try:
            await context.bot.send_message(
                chat_id=int(owner_id), text=f"🔔 {title} in {remaining} min")
        except Exception as exc:
            print(f"❌ Telegram-Push fehlgeschlagen: {exc}")


# ---------- Main ----------

def main():
    global tools, agent, fns

    # .env laden BEVOR Token gelesen wird (sonst ist TELEGRAM_BOT_TOKEN leer)
    from dotenv import load_dotenv
    load_dotenv(override=True)  # override: .env hat Vorrang vor Shell-Env

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
        print("  5. Starte erneut: uv run python needle-only/telegram.py")
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
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("list", cmd_list))
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