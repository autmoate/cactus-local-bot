"""Two candidate language-near tool contracts (command_ir spike, §3).

Both expose ONLY evidence-only text spans — never normalized calendar fields.
All semantic values (dates, durations, all_day, busy, visibility, ids) are
computed by the deterministic compiler, never by the model.

  Contract A — PHRASE-FIRST: bigger spans (change = one text, show = one query)
  Contract B — SPAN-SLOTTED: a bit more structure, still text-only

No date/until/time/end_time/horizon/duration_min:int anywhere.
"""

from __future__ import annotations

import needle

CONTRACTS = ("A", "B")

# tool name -> ordered parameter names (for span-grounding + gold serialization)
PARAMS = {
    "A": {"calendar_add": ("title", "when", "people"),
          "calendar_change": ("target", "change"),
          "calendar_remove": ("target",),
          "calendar_show": ("query",),
          "calendar_availability": ("query",)},
    "B": {"calendar_add": ("title", "when", "people"),
          "calendar_change": ("target", "new_when", "new_title"),
          "calendar_remove": ("target",),
          "calendar_show": ("when", "person"),
          "calendar_availability": ("people", "when", "duration")},
}


def _contract_a() -> dict:
    @needle.tool
    def calendar_add(title: str, when: str, people: str = "") -> str:
        """Create a calendar entry.

        Args:
            title: the entry title, copied exactly as written
            when: the time phrase from the request, copied exactly as written
            people: participant names copied exactly as written; empty if none
        """
        return ""

    @needle.tool
    def calendar_change(target: str, change: str) -> str:
        """Change an existing entry (time or title).

        Args:
            target: text identifying the existing entry, copied exactly as written
            change: the new time phrase or new title, copied exactly as written
        """
        return ""

    @needle.tool
    def calendar_remove(target: str) -> str:
        """Delete an existing entry.

        Args:
            target: text identifying the entry, copied exactly as written
        """
        return ""

    @needle.tool
    def calendar_show(query: str = "") -> str:
        """Show calendar entries.

        Args:
            query: the requested time/person phrase, copied exactly as written
        """
        return ""

    @needle.tool
    def calendar_availability(query: str) -> str:
        """Find free time for people.

        Args:
            query: the people/time/duration phrase, copied exactly as written
        """
        return ""

    return {fn.__name__: fn for fn in
            (calendar_add, calendar_change, calendar_remove, calendar_show,
             calendar_availability)}


def _contract_b() -> dict:
    @needle.tool
    def calendar_add(title: str, when: str, people: str = "") -> str:
        """Create a calendar entry.

        Args:
            title: the entry title, copied exactly as written
            when: the time phrase from the request, copied exactly as written
            people: participant names copied exactly as written; empty if none
        """
        return ""

    @needle.tool
    def calendar_change(target: str, new_when: str = "", new_title: str = "") -> str:
        """Change an existing entry's time and/or title.

        Args:
            target: text identifying the existing entry, copied exactly as written
            new_when: the new time phrase copied exactly as written; empty if
                the time does not change
            new_title: the new title copied exactly as written; empty if the
                title does not change
        """
        return ""

    @needle.tool
    def calendar_remove(target: str) -> str:
        """Delete an existing entry.

        Args:
            target: text identifying the entry, copied exactly as written
        """
        return ""

    @needle.tool
    def calendar_show(when: str = "", person: str = "") -> str:
        """Show calendar entries.

        Args:
            when: the requested time phrase copied exactly as written; empty
                for the default window
            person: the requested person copied exactly as written; empty for self
        """
        return ""

    @needle.tool
    def calendar_availability(people: str, when: str = "",
                              duration: str = "") -> str:
        """Find free time for people.

        Args:
            people: participant names copied exactly as written
            when: the requested time phrase copied exactly as written; empty
                for the default window
            duration: the requested duration copied exactly as written; empty
                for the default
        """
        return ""

    return {fn.__name__: fn for fn in
            (calendar_add, calendar_change, calendar_remove, calendar_show,
             calendar_availability)}


def build_tools(contract: str) -> dict:
    if contract == "A":
        return _contract_a()
    if contract == "B":
        return _contract_b()
    raise ValueError(f"unknown contract {contract!r}")


def tool_names(contract: str) -> list[str]:
    return list(PARAMS[contract].keys())
