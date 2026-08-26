from typing import Any

import requests


class CactusEngine:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def online(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/models", timeout=1.5)
            return response.status_code < 500
        except requests.RequestException:
            return False

    def complete(self, prompt: str, context: Any | None = None) -> str:
        messages = [
            {"role": "system", "content": "You are Gemma running locally through Cactus. Be concise."},
            {"role": "user", "content": self._prompt(prompt, context)},
        ]
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json={"model": "local", "messages": messages, "max_tokens": 512},
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            return f"Gemma/Cactus unavailable: {exc}"

    @staticmethod
    def _prompt(prompt: str, context: Any | None) -> str:
        if context is None:
            return prompt
        return f"User request: {prompt}\n\nLocal tool context/result: {context}"
