"""Semantic Router als Triage-Layer: Tool-Auswahl via Embeddings.
Routing in ~100ms (deterministisch), Needle nur noch für Argument-Extraktion.
Encoder: paraphrase-multilingual-MiniLM-L12-v2 (deutsch + englisch)."""

_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Route-Definitionen: tool_name -> Beispiel-Phrasen
# WICHTIG: Utterances müssen lang & kontextreich sein, damit sich die
# Routes in der Embedding-Space klar unterscheiden.
ROUTES = {
    "calendar_create": [
        # Termine — Schreib-Verben + "Termin"
        "erstelle einen termin zahnarzt", "erstelle einen termin beim arzt",
        "neuer termin zahnarzt", "termin anlegen zahnarzt",
        "erstelle einen termin meeting", "plane einen termin meeting",
        "ich habe morgen einen termin", "termin zahnarzt am 10.9.",
        "termin arzt nächste woche dienstag",
        "erstelle einen termin", "neuer termin", "termin anlegen",
        "plane einen termin", "termin eintragen", "kalender eintrag erstellen",
        # Erinnerungen — "Erinnerung" / "erinnern"
        "stell eine erinnerung", "erinnerung setzen", "erinner mich",
        "erinnerung wasser trinken", "stell eine erinnerung medikamente",
        "erinnerung medikamente nehmen morgen früh",
        "erinnere mich daran wasser zu trinken",
        "stell einen timer", "weck mich um 7 uhr",
        "erinnerung in 10 minuten", "erinnerung morgen früh",
        "erstelle eine erinnerung", "erstelle eine erinnerung wasser trinken",
        "erinnere mich", "erinnere mich in 30 minuten an kaffee",
        "erinnerung an zahnarzt", "stell mir eine erinnerung",
        # Aufgaben — "Aufgabe" / "Deadline"
        "erstelle eine aufgabe", "neue aufgabe", "aufgabe anlegen",
        "aufgabe bericht schreiben", "aufgabe bericht schreiben bis freitag",
        "to-do erstellen", "deadline setzen", "aufgabe hinzufügen",
        "aufgabe steuererklärung abgeben",
    ],
    "calendar_read": [
        # Lesen — "was steht an", "zeige", etc.
        "was steht diese woche an", "was steht an",
        "zeige meine termine", "zeige termine",
        "was kommt diese woche", "was habe ich diese woche",
        "kalender anzeigen", "meine erinnerungen anzeigen",
        "zeige meine erinnerungen", "zeige erinnerungen",
        "zeige meine aufgaben", "zeige aufgaben",
        "was ist diese woche geplant", "termine auflisten",
        "nächste termine anzeigen", "was ist zu tun",
        "was habe ich nächste woche für termine",
    ],
    "calendar_edit": [
        # Bearbeiten — "verschiebe", "änder", etc.
        "verschiebe den termin", "verschiebe zahnarzt",
        "termin verschieben", "termin bearbeiten",
        "änder den termin", "termin ändern",
        "änder die uhrzeit", "zeit ändern",
        "verschiebe zahnarzt auf 11:30",
        "änder den termin arzt auf morgen 14 uhr",
    ],
    "calendar_delete": [
        # Löschen — "lösche Termin", "entferne aus Kalender"
        "lösche den termin zahnarzt", "lösche den termin",
        "lösche zahnarzt", "lösche den eintrag zahnarzt",
        "entferne den termin aus dem kalender",
        "entferne aus dem kalender", "entferne den eintrag",
        "streich den termin", "termin absagen",
        "termin löschen", "lösche zahnarzt aus dem kalender",
        "erinnerung löschen", "lösche die erinnerung",
        "aufgabe löschen", "lösche die aufgabe",
    ],
    "note_write": [
        # Notiz schreiben — "merk dir", "notiere", "speichere"
        "merk dir dass feuerholz 8 euro kostet",
        "merk dir feuerholz kostet 8 euro",
        "merk dir", "notiere dass",
        "speichere die information", "speichere meine adresse",
        "notiz schreiben", "notiz speichern",
        "schreib auf dass", "behalte das im kopf",
        "notiz anlegen über feuerholz",
        "erstelle notiz", "erstelle eine notiz",
        "notiere dir dass", "speicher dir",
        "merke dir dass feuerholz 8 euro kostet",
        "merke dir feuerholz kostet 8 euro",
        "merke dir", "notiere dir feuerholz kostet 8 euro",
        "speichere notiz", "notiz erstellen",
    ],
    "note_read": [
        # Notiz lesen — "was weißt du", "zeige Notizen"
        "was weißt du über feuerholz",
        "was weißt du über das thema",
        "zeige meine notizen", "zeige notizen",
        "suche in meinen notizen", "notizen durchsuchen",
        "was hast du notiert", "notizen anzeigen",
        "lies meine notizen", "was steht in den notizen",
    ],
    "note_delete": [
        # Notiz löschen — "lösche Notiz", "vergiss Notiz"
        "lösche die notiz", "lösche die notiz feuerholz",
        "vergiss die notiz", "notiz entfernen",
        "entferne die notiz", "notiz löschen",
        "lösche notiz feuerholz", "vergiss die notiz über feuerholz",
    ],
}

# Chitchat / keine Tool-Auswahl — nur klare Nicht-Tool-Queries
NONE_ROUTE = [
    "wie geht es dir", "wie geht's", "erzähl mir einen witz",
    "was ist die hauptstadt von deutschland",
    "wer hat das internet erfunden", "wer hat das licht erfunden",
    "was ist die antwort auf alles",
    "hallo", "guten tag", "danke", "bitte",
    "wie heißt du", "wer bist du", "was kannst du",
    "hello", "how are you", "tell me a joke", "thanks",
]


class ToolRouter:
    """Embedding-basierter Tool-Router (deterministisch, ~100ms)."""

    def __init__(self, threshold: float = 0.55):
        self.threshold = threshold
        self._model = None
        self._embeddings = None
        self._tool_names = []
        self._lock = __import__("threading").Lock()

    def _ensure_loaded(self):
        """Lazy Loading von Modell und Embeddings."""
        if self._model is not None:
            return

        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(_MODEL_NAME)

        # Alle Utterances embedden
        all_utterances = []
        self._tool_names = []

        for tool_name, utterances in ROUTES.items():
            for utt in utterances:
                all_utterances.append(utt)
                self._tool_names.append(tool_name)

        # None-Route (Chitchat) hinzufügen
        for utt in NONE_ROUTE:
            all_utterances.append(utt)
            self._tool_names.append(None)

        self._embeddings = self._model.encode(
            all_utterances, convert_to_tensor=True,
            normalize_embeddings=True)

    def route(self, text: str) -> tuple:
        """Returns (tool_name, score). tool_name=None wenn unter Threshold."""
        self._ensure_loaded()

        query_emb = self._model.encode(
            text, convert_to_tensor=True, normalize_embeddings=True)

        # Cosine-Similarity mit allen Route-Utterances
        scores = (query_emb @ self._embeddings.T).squeeze(0)
        max_score, max_idx = scores.max(dim=0)

        tool_name = self._tool_names[max_idx.item()]
        score = max_score.item()

        if tool_name is None:
            # Chitchat erkannt — kein Tool
            return None, score

        if score < self.threshold:
            return None, score

        return tool_name, score


# Singleton-Instanz
router = ToolRouter()


def route_tool(text: str) -> tuple:
    """Convenience-Funktion: (tool_name, score) für einen Text."""
    return router.route(text)
