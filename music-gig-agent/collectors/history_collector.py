import json
from pathlib import Path
from typing import Any


def load_gig_history(path: Path) -> dict[str, Any]:
    """Load local user gig attendance and preference history."""
    if not path.exists():
        return {
            "listener_profile": {},
            "gigs_attended": [],
            "live_history_summary": {},
        }

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError("Expected gig history to be a JSON object")

    gigs_attended = data.get("gigs_attended", [])
    if not isinstance(gigs_attended, list):
        raise ValueError("Expected 'gigs_attended' to be a list")

    return data
