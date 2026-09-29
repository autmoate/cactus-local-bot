// Phase-1 spike: read the displayed message and ask the local Needle host for
// an event candidate. Nothing is written to a calendar; no mail is stored.
// Keep this intentionally small: all business logic lives in the Python host.

const HOST = "de.example.tb_calendar_extract";

async function displayedMessage() {
  const messages = await browser.messageDisplay.getDisplayedMessages();
  return messages && messages.length ? messages[0] : null;
}

// Flatten the MIME tree to the first body we find (no HTML parsing in Phase 1).
async function messageBodyText(messageId) {
  try {
    const full = await browser.messages.getFull(messageId);
    const walk = (part) => {
      if (!part) return "";
      if (part.body) return part.body;
      for (const child of part.parts || []) {
        const text = walk(child);
        if (text) return text;
      }
      return "";
    };
    return walk(full) || "";
  } catch (e) {
    console.error("getFull failed", e);
    return "";
  }
}

function asList(values) {
  return (values || []).filter(Boolean);
}

async function buildPayload(header) {
  return {
    type: "extract_event",
    mode: "message",
    message: {
      id: String(header.id),
      subject: header.subject || "",
      from: asList([header.author]),
      to: asList(header.recipients),
      cc: asList(header.ccList),
      received_at: header.date ? new Date(header.date).toISOString() : null,
      body_text: await messageBodyText(header.id)
    },
    selection: null
  };
}

function openPreview(result) {
  const json = JSON.stringify(result);
  const encoded = encodeURIComponent(btoa(unescape(encodeURIComponent(json))));
  browser.windows.create({
    url: browser.runtime.getURL("popup.html") + "?data=" + encoded,
    type: "popup",
    width: 440,
    height: 580
  });
}

async function onPrepare() {
  const header = await displayedMessage();
  if (!header) {
    openPreview({ status: "error", error: "Keine Nachricht geöffnet." });
    return;
  }
  const payload = await buildPayload(header);
  try {
    const result = await browser.runtime.sendNativeMessage(HOST, payload);
    openPreview(result);
  } catch (e) {
    openPreview({ status: "error", error: "Native host nicht erreichbar: " + e });
  }
}

browser.messageDisplayAction.onClicked.addListener(onPrepare);
