"""
pipeline/persistence.py

Saves and loads profiling results to disk.

Default output: <repo_root>/output/profiles/profile_<table_name>.json
Directory is created automatically. Path resolves from __file__ so it
works regardless of the CWD when uvicorn starts.

Public API:
  save_profile(result, table_name, *, output_dir, source_file, pipeline) -> Path | None
  load_profile(table_name, *, output_dir)                                -> dict | None
  list_profiles(output_dir)                                              -> list[dict]
  profile_exists(table_name, output_dir)                                 -> bool
  get_profile_path(table_name, output_dir)                               -> Path

Saved JSON format:
  {
    "_meta":          { saved_at, schema_version, table_name, source_file, pipeline },
    "table_name":     str,
    "column_profiles": [...],
    "table_summary":   {...}
  }
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pipeline.logger import get_logger
from profiling.models import ProfilingResult

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

# __file__ → DM-agent/pipeline/persistence.py
# .parent  → DM-agent/pipeline/
# .parent  → DM-agent/
_REPO_ROOT = Path(__file__).resolve().parent.parent

# All profiles land in <repo_root>/output/profiles/
DEFAULT_PROFILES_DIR: Path = _REPO_ROOT / "output" / "profiles"

# Increment when the JSON schema changes in a breaking way
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _sanitize_name(table_name: str) -> str:
    """
    Make a table name filesystem-safe: replace non-alphanumeric chars with '_',
    strip leading/trailing underscores, fall back to 'unnamed' if empty.
    """
    sanitized = re.sub(r"[^\w\-]", "_", table_name.strip())
    return sanitized.strip("_") or "unnamed"


def _ensure_dir(directory: Path) -> bool:
    """
    Create `directory` (and all parent dirs) if it does not exist.

    Returns True on success, False if creation fails.
    Failure is logged but never raises — save_profile() checks the return value
    and returns None gracefully rather than crashing the pipeline.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        logger.error(f"Could not create output directory '{directory}': {exc}")
        return False


# ---------------------------------------------------------------------------
# PUBLIC: PATH HELPER
# ---------------------------------------------------------------------------

def get_profile_path(
    table_name: str,
    output_dir: Union[Path, str] = DEFAULT_PROFILES_DIR,
) -> Path:
    """Return expected file path for a table's profile (does not check existence)."""
    safe_name = _sanitize_name(table_name)
    return Path(output_dir) / f"profile_{safe_name}.json"


# ---------------------------------------------------------------------------
# PUBLIC: SAVE
# ---------------------------------------------------------------------------

def save_profile(
    result: Union[ProfilingResult, dict],
    table_name: str,
    *,
    output_dir: Union[Path, str] = DEFAULT_PROFILES_DIR,
    source_file: str = "",
    pipeline: str = "",
) -> Optional[Path]:
    """
    Write a profiling result to disk as JSON.
    Accepts a ProfilingResult model or a plain dict.
    Injects a _meta block (saved_at, schema_version, source_file, pipeline).
    Returns the saved Path on success, None on failure (never raises).
    Overwrites any existing file for the same table_name.
    """
    output_dir = Path(output_dir)

    # Ensure the output directory exists before trying to write
    if not _ensure_dir(output_dir):
        return None

    # Normalise result to a plain Python dict
    if isinstance(result, ProfilingResult):
        payload: dict = result.model_dump()
    elif isinstance(result, dict):
        payload = dict(result)          # shallow copy — don't mutate the caller's dict
    else:
        logger.error(
            f"save_profile: unsupported result type '{type(result).__name__}'. "
            f"Expected ProfilingResult or dict."
        )
        return None

    # Inject traceability metadata at the top level
    payload["_meta"] = {
        "saved_at":       datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "table_name":     table_name,
        "source_file":    source_file,
        "pipeline":       pipeline,
    }

    file_path = get_profile_path(table_name, output_dir)

    try:
        with file_path.open("w", encoding="utf-8") as fh:
            # default=str handles any residual non-serialisable types (e.g. datetime)
            json.dump(payload, fh, indent=2, default=str, ensure_ascii=False)

        logger.info(f"  💾 Profile saved  → {file_path.name}  ({file_path.stat().st_size} bytes)")
        return file_path

    except (OSError, TypeError, ValueError) as exc:
        logger.error(f"  ✗ Failed to save profile '{file_path}': {exc}")
        return None


# ---------------------------------------------------------------------------
# PUBLIC: LOAD
# ---------------------------------------------------------------------------

def load_profile(
    table_name: str,
    *,
    output_dir: Union[Path, str] = DEFAULT_PROFILES_DIR,
) -> Optional[dict]:
    """
    Load a previously saved profile from disk.
    Returns the full JSON dict (including _meta) on success, None if missing or corrupt.

    Usage:
        profile = load_profile("orders")
        summary  = profile["table_summary"]
        columns  = profile["column_profiles"]
        saved_at = profile["_meta"]["saved_at"]
    """
    file_path = get_profile_path(table_name, output_dir)

    if not file_path.exists():
        logger.warning(
            f"load_profile: no saved profile for '{table_name}' "
            f"(looked for: {file_path})"
        )
        return None

    try:
        with file_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)

        meta = data.get("_meta", {})
        logger.info(
            f"  📂 Profile loaded  ← {file_path.name}  "
            f"(saved {meta.get('saved_at', 'unknown')})"
        )
        return data

    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"  ✗ Failed to load profile '{file_path}': {exc}")
        return None


# ---------------------------------------------------------------------------
# PUBLIC: EXISTS CHECK
# ---------------------------------------------------------------------------

def profile_exists(
    table_name: str,
    output_dir: Union[Path, str] = DEFAULT_PROFILES_DIR,
) -> bool:
    """Return True if a saved profile JSON exists for table_name."""
    return get_profile_path(table_name, output_dir).exists()


# ---------------------------------------------------------------------------
# PUBLIC: LIST ALL PROFILES
# ---------------------------------------------------------------------------

def list_profiles(
    output_dir: Union[Path, str] = DEFAULT_PROFILES_DIR,
) -> List[Dict[str, Any]]:
    """
    Return a lightweight index of every profile_*.json in the output directory.
    Each entry contains: table_name, file_path, file_size_kb, saved_at,
    source_file, pipeline, column_count, row_count, pk_candidates, schema_only.
    Unparseable files include an 'error' key instead.
    Returns [] when the directory doesn't exist yet.
    """
    output_dir = Path(output_dir)

    if not output_dir.exists():
        return []

    profiles: List[Dict[str, Any]] = []

    for json_file in sorted(output_dir.glob("profile_*.json")):
        entry: Dict[str, Any] = {
            "file_path":    str(json_file),
            "file_size_kb": round(json_file.stat().st_size / 1024, 2),
        }

        try:
            with json_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)

            meta    = data.get("_meta",         {})
            summary = data.get("table_summary", {})

            entry.update({
                "table_name":   data.get("table_name", json_file.stem.replace("profile_", "")),
                "saved_at":     meta.get("saved_at",       ""),
                "source_file":  meta.get("source_file",    ""),
                "pipeline":     meta.get("pipeline",       ""),
                "column_count": summary.get("column_count",  0),
                "row_count":    summary.get("row_count",     0),
                "pk_candidates":summary.get("pk_candidates", []),
                "schema_only":  summary.get("schema_only",  False),
            })

        except (OSError, json.JSONDecodeError, KeyError) as exc:
            entry["table_name"] = json_file.stem
            entry["error"]      = str(exc)
            logger.warning(f"list_profiles: could not read '{json_file.name}': {exc}")

        profiles.append(entry)

    return profiles
