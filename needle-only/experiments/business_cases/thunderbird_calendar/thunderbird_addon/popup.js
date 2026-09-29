// Render the candidate JSON returned by the native host. Read-only preview.

function decodeData() {
  const raw = new URLSearchParams(location.search).get("data");
  if (!raw) return null;
  try {
    return JSON.parse(decodeURIComponent(escape(atob(decodeURIComponent(raw)))));
  } catch (e) {
    return { status: "error", error: "Payload konnte nicht gelesen werden." };
  }
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function fmtWhen(c) {
  if (!c.start) return "unvollständig";
  if (c.all_day) return c.start.slice(0, 10) + " (ganztägig)";
  const end = c.end ? "–" + c.end.slice(11, 16) : "";
  return c.start.slice(0, 10) + " " + c.start.slice(11, 16) + end;
}

function render(result) {
  const el = document.getElementById("content");
  if (!result || result.status === "error") {
    el.innerHTML = '<div class="banner err">⚠️ ' +
      esc((result && result.error) || "Fehler") + "</div>";
    return;
  }
  const candidates = result.candidates || [];
  if (!candidates.length) {
    el.innerHTML = "📭 Kein eindeutiger Termin erkannt. " +
      "Markiere den relevanten Satz und versuche es erneut.";
    return;
  }
  const lines = [];
  candidates.forEach((c, i) => {
    lines.push("<h1>" + (candidates.length > 1 ? (i + 1) + ". " : "") +
      esc(c.title || "(ohne Titel)") + "</h1>");
    lines.push(row("Datum/Zeit", fmtWhen(c)));
    lines.push(row("Ort", c.location || "–"));
    lines.push(row("Status", c.status + (c.needs_review ? " · Review nötig" : "")));
    if (c.review_reasons && c.review_reasons.length) {
      lines.push('<div class="muted">' + esc(c.review_reasons.join(", ")) + "</div>");
    }
  });
  lines.push('<div class="muted">when-Span: ' + esc(result.raw_when || "–") + "</div>");
  lines.push('<div class="muted">Modell: ' + esc(result.model || "?") +
    " · " + esc(result.latency_ms || 0) + " ms</div>");
  el.innerHTML = lines.join("");
}

document.addEventListener("DOMContentLoaded", () => render(decodeData()));
