import requests


class WebSearch:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        if not self.base_url:
            return False
        try:
            response = requests.get(f"{self.base_url}/search", params={"q": "test", "format": "json"}, timeout=3)
            return response.status_code < 500
        except requests.RequestException:
            return False

    def search(self, query: str, max: int = 5) -> list[dict]:
        if not self.base_url:
            return [{"title": "nicht verfügbar", "url": "", "content": "keine Such-Instanz konfiguriert"}]
        try:
            response = requests.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "safesearch": 1},
                timeout=self.timeout,
            )
            response.raise_for_status()
            results = response.json().get("results", [])[:max]
            return [
                {"title": r.get("title"), "url": r.get("url"), "content": (r.get("content") or "")[:300]}
                for r in results
            ]
        except requests.RequestException as exc:
            return [{"title": "Websuche fehlgeschlagen", "url": "", "content": str(exc)[:200]}]
