from typing import Any

import requests


class CactusEngine:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._model_id: str | None = None

    def online(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/models", timeout=1.5)
            return response.status_code < 500
        except requests.RequestException:
            return False

    def model_id(self) -> str:
        if self._model_id is None:
            try:
                response = requests.get(f"{self.base_url}/models", timeout=5)
                response.raise_for_status()
                models = response.json().get("data") or []
                self._model_id = models[0]["id"] if models else "local"
            except Exception:
                self._model_id = "local"
        return self._model_id

    def complete(self, prompt: str, context: Any | None = None,
                 reasoning_effort: str | None = None, temperature: float | None = None) -> str:
        messages = [
            {"role": "system", "content": (
                "Du bist 'Cactus', ein lokaler Orga-Helfer. Antworte nutzerorientiert und knapp. "
                "Frag NIE, ob du ein Tool ausführen sollst - Aktionen startet dein Dispatcher. "
                "Nutze den Jahreszeit-/Kontext-Verlauf, um aufeinander aufzubauen."
            )},
            {"role": "user", "content": self._prompt(prompt, context)},
        ]
        payload: dict[str, Any] = {"model": self.model_id(), "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            return f"Gemma/Cactus unavailable: {exc}"

    def function_call(self, messages: list[dict], tools: list[dict] | None = None,
                      temperature: float | None = None, max_tokens: int | None = None) -> dict:
        payload: dict[str, Any] = {"model": self.model_id(), "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response = requests.post(f"{self.base_url}/chat/completions", json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]

    @staticmethod
    def _prompt(prompt: str, context: Any | None) -> str:
        if context is None:
            return prompt
        return f"User request: {prompt}\n\nLocal tool context/result: {context}"
