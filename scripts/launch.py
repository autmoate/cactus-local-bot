import os
import subprocess
import sys
from pathlib import Path
from time import sleep

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], check: bool = True, background: bool = False, out=None):
    if background:
        return subprocess.Popen(cmd, cwd=ROOT, stdout=out or subprocess.DEVNULL,
                                stderr=(out or subprocess.DEVNULL))
    return subprocess.run(cmd, cwd=ROOT, check=check)


def ensure_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        env.write_text((ROOT / ".env.example").read_text(), encoding="utf-8")


def start_postgres() -> None:
    load_dotenv(ROOT / ".env")
    if os.getenv("POSTGRES_AUTO_START", "1") != "1":
        print("POSTGRES_AUTO_START=0; skipping Docker Compose Postgres start.")
        return
    try:
        run(["docker", "compose", "up", "-d", "postgres"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Postgres could not be started automatically: {exc}")
        print("Continuing; the TUI will show Postgres offline until DATABASE_URL is reachable.")
        return
    for _ in range(30):
        result = subprocess.run(
            ["docker", "compose", "ps", "postgres", "--format", "{{.Health}}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if "healthy" in result.stdout:
            return
        sleep(1)
    print("Postgres did not become healthy within 30s; continuing with TUI startup.")


def start_cactus_if_requested():
    load_dotenv(ROOT / ".env")
    if os.getenv("CACTUS_AUTO_START", "0") != "1":
        return None
    model = os.getenv("CACTUS_MODEL", "./models/gemma-4-e2b")
    try:
        log_file = open("/tmp/cactus_serve.log", "ab", buffering=0)
        return run(
            ["cactus", "serve", model, "--host", "127.0.0.1", "--port", "8080",
             "--no-cloud-handoff", "--no-access-log"],
            background=True,
            out=log_file,
        )
    except FileNotFoundError:
        print("CACTUS_AUTO_START=1, but 'cactus' is not available on PATH.")
        print("Continuing; the TUI will show Cactus/Gemma offline.")
        return None


def main() -> None:
    ensure_env()
    start_postgres()
    cactus = start_cactus_if_requested()
    try:
        run([sys.executable, "tui-app.py"])
    finally:
        if cactus:
            cactus.terminate()


if __name__ == "__main__":
    main()
