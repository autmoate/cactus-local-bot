"""Identity, authorization and request scope (plan §6, §8, §21).

Adapter-neutral: Telegram (or a later PWA/Matrix) maps its Update to a
RequestContext here; the calendar service never sees transport details.
No secrets are read or logged here — AuthConfig is built by the adapter from
the environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AuthConfig:
    """Authorization policy (plan §8): user id and chat id are separate."""
    owner_user_id: int | None = None
    allowed_user_ids: set[int] = field(default_factory=set)
    allowed_chat_ids: set[int] = field(default_factory=set)
    owner_chat_id: int | None = None  # legacy private test bot (when no user id)
    allow_first_start_pairing: bool = False

    def is_user_allowed(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        return (user_id == self.owner_user_id) or (user_id in self.allowed_user_ids)

    def is_chat_allowed(self, chat_id: int) -> bool:
        return (chat_id in self.allowed_chat_ids) or (chat_id == self.owner_chat_id)

    def is_authorized(self, user_id: int | None, chat_id: int,
                      chat_type: str) -> bool:
        """Private: the user must be allowed. Group: the chat AND the sender
        must be allowed (an allowed group does not authorize every member)."""
        if chat_type == "private":
            if self.owner_user_id is None and self.owner_chat_id == chat_id:
                return True
            return self.is_user_allowed(user_id)
        return self.is_chat_allowed(chat_id) and self.is_user_allowed(user_id)


@dataclass
class RequestContext:
    actor_person_id: int
    telegram_user_id: int | None
    chat_id: int
    chat_type: str                      # 'private' | 'group'
    target_calendar_id: int             # where writes land / default read
    read_calendar_ids: list[int]        # calendars whose titles are visible
    member_person_ids: list[int]        # group members (lanes/availability)

    @property
    def is_group(self) -> bool:
        return self.chat_type == "group"

    def busy_calendar_ids(self, store) -> list[int]:
        """Calendars that define these people's busy time (plan §18): private =
        own personal calendar; group = members' personal calendars + the group
        calendar. Never other groups or same-named events elsewhere."""
        if not self.is_group:
            return list(self.read_calendar_ids)
        ids: list[int] = []
        for pid in self.member_person_ids:
            cid = store.calendar_of_person(pid)
            if cid and cid not in ids:
                ids.append(cid)
        if self.target_calendar_id not in ids:
            ids.append(self.target_calendar_id)
        return ids


def _actor(store, telegram_user_id: int | None, display_name: str,
           owner: bool) -> int:
    """Canonical identity (plan §3): only the authorized owner reconciles the
    legacy 'Ich' bucket; other allowed users get their own people row."""
    if owner:
        return store.reconcile_owner(telegram_user_id, display_name)
    return store.ensure_person(display_name, telegram_user_id)


def resolve_private(store, telegram_user_id: int | None, chat_id: int,
                    display_name: str, owner: bool = False) -> RequestContext:
    """One allowed user -> one people row + one personal calendar (plan §4)."""
    person_id = _actor(store, telegram_user_id, display_name, owner)
    personal = store.ensure_personal_calendar(person_id)
    return RequestContext(actor_person_id=person_id, telegram_user_id=telegram_user_id,
                          chat_id=chat_id, chat_type="private",
                          target_calendar_id=personal, read_calendar_ids=[personal],
                          member_person_ids=[person_id])


def resolve_group(store, chat_id: int, telegram_user_id: int | None,
                  display_name: str, group_name: str = "Gruppe",
                  owner: bool = False) -> RequestContext:
    """One allowed group chat -> one group calendar; the sender joins as member.
    Private member calendars are used for availability only, never for titles."""
    group_cal = store.ensure_group_calendar(chat_id, group_name)
    person_id = _actor(store, telegram_user_id, display_name, owner)
    store.add_member(group_cal, person_id, role="member")
    members = store.members_of(group_cal)
    return RequestContext(actor_person_id=person_id, telegram_user_id=telegram_user_id,
                          chat_id=chat_id, chat_type="group",
                          target_calendar_id=group_cal, read_calendar_ids=[group_cal],
                          member_person_ids=members)


def resolve_read_person(store, ctx: RequestContext, person_arg: str
                        ) -> tuple[list[int] | None, str]:
    """Resolve a read's `person` argument to canonical person ids (plan §4).
    '' , 'Ich' or the actor's own name -> the actor. Otherwise resolve inside
    the current scope; never guess between homonyms. Returns (ids, status)."""
    from .calendar import _canonical_person
    arg = (person_arg or "").strip()
    if not arg or arg.lower() in ("ich", "me", "mir", "mich", "myself", "i"):
        return [ctx.actor_person_id], "ok"
    actor = store.person(ctx.actor_person_id)
    if actor and _canonical_person(arg).lower() == actor["display_name"].lower():
        return [ctx.actor_person_id], "ok"
    pid, status = resolve_name(store, arg, ctx.member_person_ids)
    return ([pid] if pid is not None else None), status


def resolve_name(store, name: str, member_ids: list[int]) -> tuple[int | None, str]:
    """Resolve a participant name inside the current scope (plan §7).
    Returns (person_id, status) with status in ok/unknown/ambiguous.
    Never guesses across homonyms."""
    from .calendar import _canonical_person
    canon = _canonical_person(name)
    if not canon:
        return None, "unknown"
    rows = []
    for pid in member_ids:
        p = store.person(pid)
        if p and p["display_name"].lower() == canon.lower():
            rows.append(pid)
    if not rows:  # external person (no telegram id): exists as people row?
        with store._conn() as c:
            found = c.execute("SELECT id FROM people WHERE display_name=? COLLATE NOCASE",
                              (canon,)).fetchall()
        rows = [r["id"] for r in found]
    if len(rows) == 1:
        return rows[0], "ok"
    if len(rows) > 1:
        return None, "ambiguous"
    return None, "unknown"


def to_display_names(store, person_ids: list[int]) -> list[str]:
    return store.people_names(person_ids)
