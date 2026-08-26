from dataclasses import dataclass, field
from time import strftime


@dataclass
class AppState:
    route: str = "idle"
    cactus_online: bool = False
    needle_ready: bool = False
    postgres_online: bool = False
    last_error: str | None = None
    events: list[str] = field(default_factory=list)

    def set(self, route: str, detail: str | None = None) -> None:
        self.route = route
        if detail:
            self.events.append(f"{strftime('%H:%M:%S')} {route}: {detail}")
            self.events[:] = self.events[-8:]
