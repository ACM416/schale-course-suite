# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path


def get_bundle_dir() -> Path:
    """Read-only resource root (works both from source and PyInstaller bundle)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parent.parent


def get_work_dir() -> Path:
    """Writable directory beside executable (when frozen) or repo root (from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """Persistent user data directory (cookies, accounts, shared AI config, logs)."""
    env_dir = os.environ.get("SCHALE_DATA_DIR", "").strip()
    base = Path(env_dir).expanduser().resolve() if env_dir else (get_work_dir() / "data")
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_template_dir(platform_name: str) -> str:
    return str(get_bundle_dir() / "platforms" / platform_name / "templates")


def get_static_dir(platform_name: str) -> str:
    return str(get_bundle_dir() / "platforms" / platform_name / "static")


def get_port(env_name: str, default_port: int) -> int:
    try:
        return int(os.environ.get(env_name, default_port))
    except Exception:
        return default_port
