"""Semantic Router als Triage-Layer: Tool-Auswahl via Embeddings.
Routing in ~100ms (deterministisch), Needle nur noch für Argument-Extraktion.
Encoder: paraphrase-multilingual-MiniLM-L12-v2 (deutsch + englisch)."""

_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Route-Definitionen: tool_name -> Beispiel-Phrasen
# WICHTIG: Utterances müssen lang & kontextreich sein, damit sich die
# Routes in der Embedding-Space klar unterscheiden.
ROUTES = {
    "calendar_create": [
        # Termine
        "erstelle einen termin zahnarzt", "erstelle einen termin beim arzt",
        "neuer termin zahnarzt", "termin anlegen zahnarzt",
        "erstelle einen termin meeting", "plane einen termin meeting",
        "ich habe morgen einen termin", "termin zahnarzt am 10.9.",
        "termin arzt nächste woche dienstag",
        "erstelle einen termin", "neuer termin", "termin anlegen",
        "plane einen termin", "termin eintragen", "kalender eintrag erstellen",
        # Termine für andere Personen (NEU: Gruppen-Support)
        "erstelle einen termin zahnarzt für lisa",
        "termin für lisa am dienstag um 9 uhr",
        "erstelle einen termin für max",
        "lisa hat am dienstag um 9 uhr einen termin",
        "trage einen termin für lisa ein",
        # Erinnerungen
        "stell eine erinnerung", "erinnerung setzen", "erinner mich",
        "erinnerung wasser trinken", "stell eine erinnerung medikamente",
        "erinnerung medikamente nehmen morgen früh",
        "erinnere mich daran wasser zu trinken",
        "stell einen timer", "weck mich um 7 uhr",
        "erinnerung in 10 minuten", "erinnerung morgen früh",
        "erstelle eine erinnerung", "erstelle eine erinnerung wasser trinken",
        "erinnere mich", "erinnere mich in 30 minuten an kaffee",
        "erinnerung an zahnarzt", "stell mir eine erinnerung",
        # Aufgaben
        "erstelle eine aufgabe", "neue aufgabe", "aufgabe anlegen",
        "aufgabe bericht schreiben", "aufgabe bericht schreiben bis freitag",
        "to-do erstellen", "deadline setzen", "aufgabe hinzufügen",
        "aufgabe steuererklärung abgeben",
        # Abwesenheiten (mehrtägig, kollidiert nicht)
        "trage urlaub ein", "ich habe urlaub von 7.9. bis 11.9.",
        "urlaub vom 7.9. bis 11.9.", "ich bin verreist nächste woche",
        "ich bin vom 7.9. bis 11.9. abwesend", "trage abwesenheit ein",
        "ich bin krank morgen", "krankmeldung eintragen",
        "urlaub eintragen im kalender", "abwesend vom bis",
    ],
    "calendar_read": [
        # Lesen
        "was steht diese woche an", "was steht an",
        "zeige meine termine", "zeige termine",
        "was kommt diese woche", "was habe ich diese woche",
        "kalender anzeigen", "meine erinnerungen anzeigen",
        "zeige meine erinnerungen", "zeige erinnerungen",
        "zeige meine aufgaben", "zeige aufgaben",
        "was ist diese woche geplant", "termine auflisten",
        "nächste termine anzeigen", "was ist zu tun",
        "was habe ich nächste woche für termine",
        "zeige meinen kalender diese woche",
    ],
    "calendar_edit": [
        # Bearbeiten
        "verschiebe den termin", "verschiebe zahnarzt",
        "termin verschieben", "termin bearbeiten",
        "änder den termin", "termin ändern",
        "änder die uhrzeit", "zeit ändern",
        "verschiebe zahnarzt auf 11:30",
        "änder den termin arzt auf morgen 14 uhr",
    ],
    "calendar_delete": [
        # Löschen
        "lösche den termin zahnarzt", "lösche den termin",
        "lösche zahnarzt", "lösche den eintrag zahnarzt",
        "entferne den termin aus dem kalender",
        "entferne aus dem kalender", "entferne den eintrag",
        "streich den termin", "termin absagen",
        "termin löschen", "lösche zahnarzt aus dem kalender",
        "erinnerung löschen", "lösche die erinnerung",
        "aufgabe löschen", "lösche die aufgabe",
        "lösche urlaub", "entferne urlaub aus dem kalender",
    ],
    "calendar_filter": [
        # Gruppen-Abfragen (Kalender einer Person anzeigen)
        "wann hat lisa diese woche termine",
        "wann hat max diese woche termine",
        "termine von lisa diese woche",
        "kalender von lisa anzeigen",
        "zeige termine für lisa",
        "was hat lisa diese woche",
        "termine für person x anzeigen",
        "kalender filter für lisa",
        "wann hat die gruppe termine",
        "termine der gruppe diese woche",
        "wann hat person x diese woche welche termine",
    ],
    "free_slots": [
        # Gemeinsame freie Slots / Verfügbarkeit (NEU: Gruppen-Support)
        "wann haben lisa und max gemeinsame zeit",
        "wann sind lisa und max gleichzeitig frei",
        "finde einen gemeinsamen termin für lisa und max",
        "wann ist lisa verfügbar",
        "wann ist lisa frei",
        "freie slots für die gruppe diese woche",
        "wann können wir uns treffen",
        "suche einen termin wo alle zeit haben",
        "wann haben wir beide zeit",
        "gemeinsame freie zeiten finden",
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
