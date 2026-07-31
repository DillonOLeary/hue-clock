import os
from dataclasses import dataclass
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "hue_clock"
TIME_TRACKING_DB = STATE_DIR / "time_tracking.db"
CAPACITIES_NOTE_DB = STATE_DIR / "capacities_note.db"
LOCK_FILE = Path.home() / ".local" / "state" / "hue_clock.lock"
LOG_FILE = Path.home() / "Library" / "Logs" / "hue-clock.log"
ENV_FILE = Path.home() / ".config" / "hue_clock" / ".env"


@dataclass(frozen=True)
class Config:
    capacities_token: str | None
    bridge_ip: str | None
    bridge_key: str | None
    light_name: str | None


def load_config() -> Config:
    _load_env_file()
    return Config(
        capacities_token=os.environ.get("CAPACITIES_API_TOKEN"),
        bridge_ip=os.environ.get("HUE_BRIDGE_IP"),
        bridge_key=os.environ.get("HUE_APP_KEY"),
        light_name=os.environ.get("FOCUS_LIGHT_NAME"),
    )


def require(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"{name} is not set (env var or .env file)")
    return value


def _load_env_file() -> None:
    """Load .env into the environment without overriding existing variables.

    Already-set environment variables always win. ~/.config/hue_clock/.env is
    the canonical location — the app bundle has no meaningful working
    directory — with an upward walk from cwd so `uv run` inside the repo
    keeps working.
    """
    for env_path in (ENV_FILE, *(d / ".env" for d in (Path.cwd(), *Path.cwd().parents))):
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())
            return
