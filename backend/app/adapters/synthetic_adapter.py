import json
from pathlib import Path

from app.schemas.event import Event


def load_events(path: str | Path) -> list[Event]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [Event.model_validate(item) for item in raw]


def load_customer_history(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
