"""Automated history-channel video pipeline."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / ".cache"
BUILD_DIR = REPO_ROOT / "build"
