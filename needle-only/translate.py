"""DE→EN-Übersetzung mit Helsinki-NLP/opus-mt_tiny_deu-eng (lokal, ~30 MB, CPU).
Lädt lazy beim ersten Aufruf; ohne Modell greift ein Pass-through-Fallback."""
_MODEL = "Helsinki-NLP/opus-mt_tiny_deu-eng"

_tok = None
_model = None
_failed = False


def _load():
    global _tok, _model, _failed
    if _tok is not None or _failed:
        return _tok is not None
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        _tok = AutoTokenizer.from_pretrained(_MODEL)
        _model = AutoModelForSeq2SeqLM.from_pretrained(_MODEL)
        _model.eval()
        globals()["_model"] = _model
        return True
    except Exception:
        _failed = True
        _tok = None
        return False


def de2en(text: str) -> tuple[str, bool]:
    """Übersetzt DE→EN; liefert (text, translated_flag)."""
    if not text.strip():
        return text, False
    if not _load():
        return text, False
    try:
        import torch
        with torch.no_grad():
            batch = _tok(text, return_tensors="pt", truncation=True, max_length=256)
            out = _model.generate(**batch, max_new_tokens=128, num_beams=1)
        return _tok.batch_decode(out, skip_special_tokens=True)[0].strip(), True
    except Exception:
        return text, False
