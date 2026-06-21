import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
STATE_PATH = BASE_DIR / "output" / "scheduled_run_state.json"
LOG_PATH = BASE_DIR / "output" / "scheduled_pipeline.log"
RUN_INTERVAL = timedelta(days=3)


PIPELINE_COMMAND = [
    sys.executable,
    "-u",
    "main.py",
    "--spotify",
    "--save-spotify-snapshot",
    "--llm-taste-profile",
    "--collect-gigs",
    "--llm-gig-enrichment",
    "--confirm-openai-cost",
    "--confirm-openai-search-cost",
    "--email-report",
]


def main() -> None:
    args = parse_args()
    now = datetime.now().astimezone()
    state = load_state()

    if not args.force and not should_run(now, state):
        last_success = state.get("last_success_at", "never")
        log(f"Skipped scheduled run at {now.isoformat()} because last success was {last_success}.")
        return

    log(f"Starting scheduled pipeline at {now.isoformat()}.")
    result = subprocess.run(
        PIPELINE_COMMAND,
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        log(result.stdout.rstrip())
    if result.stderr:
        log(result.stderr.rstrip())

    if result.returncode != 0:
        log(f"Scheduled pipeline failed with exit code {result.returncode}.")
        raise SystemExit(result.returncode)

    save_state({"last_success_at": datetime.now().astimezone().isoformat()})
    log("Scheduled pipeline completed successfully.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the music gig pipeline every 3 days.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run now even if the previous successful scheduled run was less than 3 days ago.",
    )
    return parser.parse_args()


def should_run(now: datetime, state: dict[str, str]) -> bool:
    last_success_raw = state.get("last_success_at")
    if not last_success_raw:
        return True

    try:
        last_success = datetime.fromisoformat(last_success_raw)
    except ValueError:
        return True

    return now - last_success >= RUN_INTERVAL


def load_state() -> dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict[str, str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat()
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] {message}\n")
    print(message)


if __name__ == "__main__":
    main()
