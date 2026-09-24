"""Deterministic Telegram-adapter tests (plan §24): authorization, mention via
Telegram entities (rename-proof), reply trigger. No network, no model — the
service is injected."""
import asyncio
from pathlib import Path

from local_calendar.calendar import CalendarStore
from local_calendar.identity import AuthConfig
from local_calendar.service import Decision
from local_calendar.telegram import KalenderPinBot


class FakeService:
    def __init__(self):
        self.prepared: list[str] = []
        self.confirmed: list[str] = []

    def prepare(self, text, ctx):
        self.prepared.append(text)
        return Decision("no_action", message="ok")

    def confirm(self, token, ctx):
        self.confirmed.append(token)
        return {"ok": True, "message": "done"}

    def cancel(self, token, ctx):
        pass


class _Entity:
    def __init__(self, type_, offset, length):
        self.type, self.offset, self.length = type_, offset, length


class _Chat:
    def __init__(self, cid, ctype, title="Chat"):
        self.id, self.type, self.title = cid, ctype, title

    async def send_action(self, *a, **k):
        pass


class _User:
    def __init__(self, uid, name="User"):
        self.id, self.full_name = uid, name


class _Msg:
    def __init__(self, text, chat, entities=None, reply_to=None):
        self.text, self.chat, self.entities = text, chat, entities
        self.reply_to_message = reply_to
        self.replies: list[str] = []

    async def reply_text(self, t, **k):
        self.replies.append(t)

    async def reply_photo(self, png, **k):
        self.replies.append("<photo>")


class _Update:
    def __init__(self, chat, user, text, entities=None, reply_to=None):
        self.effective_chat, self.effective_user = chat, user
        self.message = _Msg(text, chat, entities, reply_to)


def _bot(tmp_path, username="needle2orga_bot", auth=None, bot_id=42):
    store = CalendarStore(tmp_path / "t.db")
    svc = FakeService()
    bot = KalenderPinBot(svc, store, auth or AuthConfig(
        owner_user_id=1, allowed_user_ids={2}, allowed_chat_ids={500}))
    bot.username, bot.bot_id = username, bot_id
    return bot, svc


def _mention_entity(text, bot_username):
    at = f"@{bot_username}"
    return [_Entity("mention", text.index(at), len(at))]


# --------------------------------------------------------------- private flow
def test_private_allowed_processed(tmp_path):
    bot, svc = _bot(tmp_path)
    upd = _Update(_Chat(1, "private"), _User(1), "Was habe ich morgen?")
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == ["Was habe ich morgen?"]


def test_private_unknown_user_never_runs(tmp_path):
    bot, svc = _bot(tmp_path)
    upd = _Update(_Chat(999, "private"), _User(999), "Lösch alles")
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == []  # plan §8: no inference, no DB, no reply


# ------------------------------------------------------------------ group flow
def test_group_without_mention_ignored(tmp_path):
    bot, svc = _bot(tmp_path)
    upd = _Update(_Chat(500, "group"), _User(2), "einfach so gequatscht")
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == []  # plan §9: no inference before addressing


def test_group_mention_processed_and_stripped(tmp_path):
    bot, svc = _bot(tmp_path)
    text = "@needle2orga_bot trag Freitag 18 Uhr Abendessen ein"
    upd = _Update(_Chat(500, "group"), _User(2), text,
                  entities=_mention_entity(text, "needle2orga_bot"))
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == ["trag Freitag 18 Uhr Abendessen ein"]


def test_group_reply_to_bot_processed(tmp_path):
    bot, svc = _bot(tmp_path, bot_id=42)
    reply = _Msg("frühere Bot-Antwort", _Chat(500, "group"))
    reply.from_user = _User(42)  # the bot itself
    upd = _Update(_Chat(500, "group"), _User(2), "und lösch Zahnarzt",
                  reply_to=reply)
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == ["und lösch Zahnarzt"]


def test_group_unknown_sender_ignored(tmp_path):
    bot, svc = _bot(tmp_path)
    text = "@needle2orga_bot lösch alles"
    upd = _Update(_Chat(500, "group"), _User(999), text,
                  entities=_mention_entity(text, "needle2orga_bot"))
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == []  # allowed chat but unknown sender


def test_unknown_group_ignored(tmp_path):
    bot, svc = _bot(tmp_path)
    text = "@needle2orga_bot trag was ein"
    upd = _Update(_Chat(777, "group"), _User(2), text,
                  entities=_mention_entity(text, "needle2orga_bot"))
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == []


def test_rename_works_without_code_change(tmp_path):
    """A rename to @kalender_pin_bot must not require a code change (plan §2)."""
    bot, svc = _bot(tmp_path, username="kalender_pin_bot")
    text = "@kalender_pin_bot was steht Freitag an?"
    upd = _Update(_Chat(500, "group"), _User(2), text,
                  entities=_mention_entity(text, "kalender_pin_bot"))
    asyncio.run(bot.on_message(upd, None))
    assert svc.prepared == ["was steht Freitag an?"]


# ----------------------------------------------------------------- confirmation
class _CallbackQuery:
    def __init__(self, data):
        self.data = data
        self.edited: list[str] = []

    async def answer(self):
        pass

    async def edit_message_text(self, t, **k):
        self.edited.append(t)


class _CbUpdate:
    def __init__(self, chat, user, data):
        self.effective_chat, self.effective_user = chat, user
        self.callback_query = _CallbackQuery(data)
        self.message = _Msg("", chat)


def test_confirm_and_cancel_callbacks(tmp_path):
    bot, svc = _bot(tmp_path)
    upd = _CbUpdate(_Chat(1, "private"), _User(1), "cfm:tok123")
    asyncio.run(bot.on_callback(upd, None))
    assert svc.confirmed == ["tok123"]
    upd2 = _CbUpdate(_Chat(1, "private"), _User(1), "cnl:tok999")
    asyncio.run(bot.on_callback(upd2, None))
    assert svc.confirmed == ["tok123"]  # cancel does not confirm
