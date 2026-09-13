"""Playwright UI verification (run manually): uv run python tests/ui_check.py"""

from playwright.sync_api import sync_playwright

from local_calendar.agent import Agent
from local_calendar.app import build_app
from local_calendar.calendar import CalendarStore

PORT = 7890


def grid_hotels(page, weeks: int = 0) -> int:
    page.get_by_role("tab", name="Calendar").click()
    page.wait_for_timeout(2500)
    for _ in range(weeks):
        page.get_by_role("button", name="Woche ▶").click()
        page.wait_for_timeout(800)
    count = page.inner_text(".cal").count("Hotel-Checkin")
    for _ in range(weeks):
        page.get_by_role("button", name="◀ Woche").click()
        page.wait_for_timeout(800)
    return count


def run(page, text: str) -> str:
    page.get_by_role("tab", name="Assistant").click()
    box = page.get_by_placeholder("Pack mir morgen Nachmittag Zahnarzt rein.")
    box.fill(text)
    page.get_by_role("button", name="Run").click()
    page.wait_for_timeout(12000)
    return page.inner_text("body")


def main() -> None:
    store = CalendarStore("data/ui_test.db")
    agent = Agent(store, mode="needle")
    app = build_app(agent)
    app.launch(server_name="127.0.0.1", server_port=PORT, prevent_thread_lock=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{PORT}", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        out = run(page, "Erstelle mir für den 19.9. einen Termin: Hotel-Checkin 17Uhr")
        print("A) CREATE:", [l for l in out.splitlines() if "Erstellt" in l][:1])
        page.get_by_role("tab", name="Calendar").click()
        page.wait_for_timeout(3000)
        print("B) GRID: Hotel-Checkin count:", grid_hotels(page, 1), "(expected 1, week+1)")

        out = run(page, "Verschiebe Hotel-Checkin auf den 20.9. um 10 Uhr")
        print("C) MOVE:", [l for l in out.splitlines() if "Verschoben" in l][:1])
        page.get_by_role("tab", name="Calendar").click()
        page.wait_for_timeout(3000)
        print("D) GRID after move: count:", grid_hotels(page, 1), "(expected 1, week+1)")

        out = run(page, "Termin Hotel-Checkin löschen")
        print("E) DELETE:", [l for l in out.splitlines() if "Gelöscht" in l][:1])
        page.get_by_role("tab", name="Calendar").click()
        page.wait_for_timeout(3000)
        print("F) GRID after delete: count:", grid_hotels(page, 0) + grid_hotels(page, 1), "(expected 0+0)")
        browser.close()
    print("done")


if __name__ == "__main__":
    main()
