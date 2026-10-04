"""Runtime paths. Read from the environment at call time so tests can override them."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root: Path
    db_path: Path
    sources_dir: Path
    photo_dir: Path
    thumb_dir: Path
    secret: str
    password: str


def get_settings() -> Settings:
    root = ROOT
    data = root / "data"
    secret = os.environ.get("CAPTURE_SECRET")
    if not secret:
        secret_path = data / "secret.key"
        if secret_path.exists():
            secret = secret_path.read_text(encoding="utf-8").strip()
        else:
            secret = "local-dev-secret-change-me"
    return Settings(
        root=root,
        db_path=Path(os.environ.get("CAPTURE_DB", data / "app.db")),
        sources_dir=Path(os.environ.get("CAPTURE_SOURCES", data / "sources")),
        photo_dir=Path(os.environ.get("CAPTURE_PHOTO_DIR", data / "photos")),
        thumb_dir=Path(os.environ.get("CAPTURE_THUMB_DIR", data / "thumbs")),
        secret=secret,
        password=os.environ.get("EQUIPMENT_PASSWORD", "local-visit"),
    )
