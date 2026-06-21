import json
from pathlib import Path
from typing import Any


def load_gigs(path: Path) -> list[dict[str, Any]]:
    """Load mock Birmingham gig listings from local JSON."""
    # TODO: Replace this with web search, venue feeds, or ticketing APIs.
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    gigs = data.get("gigs", [])
    if not isinstance(gigs, list):
        raise ValueError("Expected 'gigs' to be a list")
    return gigs
