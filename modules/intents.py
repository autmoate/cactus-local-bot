from dataclasses import dataclass
from math import sqrt

EXEMPLARS = {
    "upcoming": [
        "welche termine stehen an?", "was habe ich morgen vor?", "was ist diese woche los?",
        "offene aufgaben", "was steht an?", "wann ist mein nächster termin?",
    ],
    "todo_write": [
        "create todo Zahnarzt morgen 9", "erinnere mich an den Anruf", "füge ein To-do hinzu",
        "erstelle eine aufgabe", "merke vor: milch kaufen",
    ],
    "calendar_write": [
        "add calendar event Meeting morgen 15", "lege einen termin an",
        "plane einen termin für dienstag", "trage den zahnarzt am freitag ein",
    ],
    "inventory_write": [
        "füge 3 sack grillkohle zum inventar hinzu", "add inventory hammer",
        "inventar aufnehmen: 5 äpfel", "lagerbestand erfassen",
    ],
    "inventory_read": [
        "was haben wir alles im inventar?", "wie viel kohle ist im inventar?",
        "was ist auf lager?", "was haben wir im bestand?",
    ],
    "search_read": [
        "suche nach wissen über cactus", "such mir die notiz von letzter woche",
        "finde das wissen zu zeitarbeit", "suche meine todos",
        "was haben wir in den aufzeichnungen",
    ],
    "db_status": [
        "zeig mir die datenbank", "was ist in der datenbank", "status der einträge",
        "wie viele zeilen hast du",
    ],
    "web": [
        "durchsuche das internet nach cactus", "suche im web nach raspberry pi",
        "googel das wort", "was sagt das internet dazu",
    ],
    "meta": [
        "was kannst du?", "welche aufgaben deckst du ab?", "what can you do?",
        "wofür bist du da?",
    ],
    "off_topic": [
        "wie ist das wetter?", "erklär mir quantenphysik",
        "was ist die hauptstadt von frankreich?", "zeichne bitte ein bild",
    ],
    "smalltalk": [
        "hi", "hallo", "danke", "ok", "aha", "guten morgen",
    ],
}

READ_KINDS = {"upcoming", "search_read", "inventory_read", "db_status", "web"}
WRITE_KINDS = {"todo_write", "calendar_write", "inventory_write", "knowledge_write"}


@dataclass
class Intent:
    kind: str
    domain: str | None = None
    horizon: str = "week"
    text: str = ""
    score: float = 0.0
    second: str | None = None
    second_score: float = 0.0

    @property
    def local(self) -> bool:
        return self.kind in READ_KINDS or self.kind in WRITE_KINDS


def _norm(v):
    n = sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class IntentClassifier:
    def __init__(self, store, embed, threshold: float = 0.72):
        self.store = store
        self.embed = embed
        self.threshold = threshold
        self._cached: dict[str, list[list[float]]] | None = None

    def _vectors(self) -> dict[str, list[list[float]]]:
        if self._cached is not None:
            return self._cached
        cached = self.store.load_intent_exemplars()
        need = {k: len(v) for k, v in EXEMPLARS.items()}
        if cached and all(len(cached.get(k, [])) >= n for k, n in need.items()):
            self._cached = cached
            return cached
        built = {k: [_norm(self.embed.embed(p)) for p in phrs] for k, phrs in EXEMPLARS.items()}
        self.store.save_intent_exemplars({k: phrs for k, phrs in EXEMPLARS.items()}, built)
        self._cached = built
        return built

    def classify(self, text: str) -> Intent:
        qv = _norm(self.embed.embed(text))
        best_k, best_s, second_k, second_s = None, -2.0, None, -2.0
        for kind, vecs in self._vectors().items():
            for v in vecs:
                score = sum(a * b for a, b in zip(qv, v))
                if score > best_s:
                    second_k, second_s = best_k, best_s
                    best_k, best_s = kind, score
                elif score > second_s:
                    second_k, second_s = kind, score
        if best_k is None or best_s < self.threshold:
            return Intent("unclear", text=text, score=best_s if best_s > -2.0 else 0.0,
                          second=second_k, second_score=second_s if second_s > -2.0 else 0.0)
        return Intent(best_k, text=text, score=best_s, second=second_k, second_score=second_s)
