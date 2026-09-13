"""Telegram-Bot „needle 📌" für gemma-first.

Nutzt GemmaPipeline: Text → Gemma FC (NLU) → v6-Planner →
Y/N-Approval → Datoms.

Start: uv run python gemma-first/bot.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(override=True)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from format import fmt_dt, fmt_day
from nlu import GemmaNLU
from pipeline import GemmaPipeline

from world.model import SourceContext, TransactionPlan
from world.store import WorldStore

BOT_NAME = "needle 📌 gemma"

# Pending Approvals: chat_id → {"plan": ..., "source": ...}
_pending: dict[int, dict] = {}

# Globals (werden in main() gesetzt)
pipeline: GemmaPipeline | None = None
store: WorldStore | None = None


# ---------- Helpers ----------

def _approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ja", callback_data="approve"),
        InlineKeyboardButton("❌ Nein", callback_data="reject"),
    ]])


def _fmt_plan(plan: TransactionPlan) -> str:
    """Formatiert den Plan human-readable."""
    return plan.summary


# ---------- Commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"👋 Hallo! Ich bin {BOT_NAME}.\n\n"
        "Ich kann dir helfen, deine Termine und Erinnerungen zu verwalten.\n"
        "Beispiele:\n"
        "  • 'Zahnarzt morgen um 14 Uhr'\n"
        "  • 'Erinnere mich in 10 Minuten an Wasser'\n"
        "  • 'Was habe ich am Mittwoch?'\n"
        "  • 'Julia bringt den Beamer'\n"
        "  • 'Der WLAN-Code ist geheim123'\n"
        "  • 'Wie lautet der WLAN-Code?'\n\n"
        "Schreib mir einfach, was du brauchst!"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Status des World Stores."""
    if store is None:
        await update.message.reply_text("❌ Store nicht initialisiert.")
        return
    from world.model import DatomRecord
    datoms = store.current_datoms()
    entities = {d.entity_id for d in datoms}
    txs = store.last_tx_id() or 0
    await update.message.reply_text(
        f"📊 World State:\n"
        f"  Entities: {len(entities)}\n"
        f"  Datoms:   {len(datoms)}\n"
        f"  Tx:       {txs}"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ Ich bin needle, dein Orga-Assistent.\n\n"
        "Termine:\n"
        "  • 'Zahnarzt morgen um 14 Uhr'\n"
        "  • 'Verschiebe Zahnarzt auf 16 Uhr'\n"
        "  • 'Sage Zahnarzt ab'\n"
        "Erinnerungen:\n"
        "  • 'Erinnere mich in 10 Minuten an Wasser'\n"
        "Fragen:\n"
        "  • 'Was habe ich am Mittwoch?'\n"
        "  • 'Habe ich Termine?'\n"
        "Fakten:\n"
        "  • 'Der WLAN-Code ist geheim123'\n"
        "  • 'Wie lautet der WLAN-Code?'\n"
        "System:\n"
        "  • 'Status', 'Undo'"
    )


# ---------- Message Handling ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Haupt-Handler für Text-Nachrichten."""
    if pipeline is None:
        await update.message.reply_text("❌ Pipeline nicht initialisiert.")
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    chat_id = update.effective_chat.id
    source = SourceContext(
        source_type="telegram",
        source_id=f"{chat_id}:{update.message.message_id}",
        actor="ich",
        raw_text=text,
    )

    t0 = time.time()
    try:
        responses = pipeline.handle(text, source)
    except Exception as exc:
        await update.message.reply_text(f"❌ Fehler: {exc}")
        return
    elapsed = time.time() - t0

    for resp in responses:
        if resp.requires_approval and resp.plan:
            _pending[chat_id] = {"plan": resp.plan, "source": source}
            await update.message.reply_text(
                resp.text,
                reply_markup=_approval_keyboard(),
            )
        else:
            await update.message.reply_text(resp.text)

    print(f"[{elapsed:.1f}s] {text[:50]} → {len(responses)} Response(s)")


# ---------- Approval Callbacks ----------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Y/N-Approval für Writes."""
    query = update.callback_query
    chat_id = query.message.chat_id
    data = query.callback_data

    if data not in ("approve", "reject"):
        await query.answer()
        return

    pending = _pending.pop(chat_id, None)
    if pending is None:
        await query.answer(text="Kein pending Approval.")
        return

    plan = pending["plan"]
    source = pending["source"]

    if data == "approve":
        try:
            tx_id = pipeline.apply(plan, source)
            await query.edit_message_text(
                f"✅ Gespeichert (tx {tx_id}).\n\n{plan.summary}"
            )
        except Exception as exc:
            await query.edit_message_text(f"❌ Fehler beim Speichern: {exc}")
    else:
        await query.edit_message_text("❌ Abgebrochen.")

    await query.answer()


# ---------- Main ----------

def main() -> None:
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
        print("  5. Starte erneut: uv run python gemma-first/bot.py")
        sys.exit(1)

    # World-Stack aufsetzen
    global pipeline
    print(f"{BOT_NAME} — Lade World Store...")
    from modules.config import load_config
    cfg = load_config()
    store = WorldStore(cfg.database_url)
    pipeline = GemmaPipeline(store, GemmaNLU(cfg.cactus_base_url))

    # Bot konfigurieren
    app = Application.builder().token(token).build()

    # Handler registrieren
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print(f"{BOT_NAME} — Telegram-Bot läuft (Polling)...")
    print(f"  Schicke /start an deinen Bot, um dich zu registrieren.")
    print(f"  Beenden: Ctrl+C")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
