"""Persistent UI configuration (${XDG_CONFIG_HOME:-~/.config}/nba-scoreboard-widget/config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CONFIG_DIR_NAME: Final[str] = "nba-scoreboard-widget"
CONFIG_FILE_NAME: Final[str] = "config.json"
DEFAULT_SCALE: Final[float] = 1.0
MIN_SCALE: Final[float] = 0.7
MAX_SCALE: Final[float] = 2.2
DEFAULT_COLUMNS: Final[int] = 2
MIN_COLUMNS: Final[int] = 1
MAX_COLUMNS: Final[int] = 8


@dataclass(frozen=True)
class AppConfig:
    scale: float = DEFAULT_SCALE
    columns: int = DEFAULT_COLUMNS
    # Top-left corner saved after a drag; None places the widget at the top-left of the screen.
    position: tuple[int, int] | None = None


def config_file_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def clamp_scale(value: object) -> float:
    try:
        scale = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_SCALE
    return max(MIN_SCALE, min(MAX_SCALE, round(scale, 2)))


def clamp_columns(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_COLUMNS
    return max(MIN_COLUMNS, min(MAX_COLUMNS, int(value)))


def load_config() -> AppConfig:
    """Read the config; a missing or unreadable file yields the defaults and is rewritten."""
    path = config_file_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    ui = raw.get("ui") if isinstance(raw, dict) and isinstance(raw.get("ui"), dict) else {}
    config = AppConfig(
        scale=clamp_scale(ui.get("scale", DEFAULT_SCALE)),
        columns=clamp_columns(ui.get("columns", DEFAULT_COLUMNS)),
        position=_coerce_position(ui.get("position")),
    )
    if raw != _serialize(config):
        save_config(config)
    return config


def save_config(config: AppConfig) -> None:
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_serialize(config), indent=2), encoding="utf-8")


def _coerce_position(value: object) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return None
    return (value[0], value[1])


def _serialize(config: AppConfig) -> dict[str, object]:
    return {
        "ui": {
            "scale": clamp_scale(config.scale),
            "columns": clamp_columns(config.columns),
            "position": list(config.position) if config.position else None,
        }
    }
