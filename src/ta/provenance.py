"""Immutable-enough build metadata for a running trading process.

The version alone is not deployment provenance: a changed config or a dirty
worktree can make two processes report the same version while behaving
differently. This module records the smallest useful identity:

  * package version
  * git commit/branch/dirty state
  * deterministic fingerprint of every config file
  * process and Python runtime details

The runner writes it at startup under the ignored data directory. The dashboard
and deployment verifier read it without exposing secrets.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__

SOURCE_PREFIXES = ("src/", "scripts/", "config/", "deploy/", ".github/")
SOURCE_FILES = {"VERSION", "pyproject.toml", "Makefile"}


def is_source_path(path: str) -> bool:
    """Whether a changed path affects executable/configurable production code."""
    return path in SOURCE_FILES or path.startswith(SOURCE_PREFIXES)


def _git(project: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def config_fingerprint(config_dir: Path) -> tuple[str, list[dict[str, str]]]:
    """Hash every regular file under config/ by relative path and content."""
    config_dir = Path(config_dir)
    rows: list[dict[str, str]] = []
    digest = hashlib.sha256()
    if not config_dir.exists():
        return digest.hexdigest(), rows
    for path in sorted(config_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name == ".DS_Store":
            continue
        relative = path.relative_to(config_dir).as_posix()
        content = path.read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        rows.append({"path": relative, "sha256": file_hash})
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest(), rows


def source_identity(project: Path) -> dict[str, Any]:
    status = _git(project, "status", "--porcelain", "--untracked-files=normal")
    status_paths = [line[3:] for line in (status or "").splitlines() if len(line) > 3]
    exact_tag = _git(project, "describe", "--tags", "--exact-match", "HEAD") or ""
    nearest_tag = exact_tag or _git(project, "describe", "--tags", "--abbrev=0", "HEAD") or ""
    commits_since_tag = 0
    changed_since_tag: list[str] = []
    if nearest_tag and not exact_tag:
        raw_count = _git(project, "rev-list", "--count", f"{nearest_tag}..HEAD") or "0"
        try:
            commits_since_tag = int(raw_count)
        except ValueError:
            commits_since_tag = 0
        raw_paths = _git(project, "diff", "--name-only", f"{nearest_tag}..HEAD") or ""
        changed_since_tag = [path for path in raw_paths.splitlines() if path]
    return {
        "git_sha": _git(project, "rev-parse", "HEAD") or "",
        "git_branch": _git(project, "branch", "--show-current") or "",
        "git_tag": exact_tag,
        "nearest_tag": nearest_tag,
        "commits_since_tag": commits_since_tag,
        "changed_since_tag": changed_since_tag,
        "source_changed_since_tag": any(is_source_path(path) for path in changed_since_tag),
        "git_dirty": bool(status),
        "source_dirty": any(is_source_path(path) for path in status_paths),
        "git_status_entries": len(status.splitlines()) if status else 0,
    }


def collect_provenance(
    config: Any,
    *,
    context: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Build a secret-free provenance record for one runner process."""
    project = Path(config.project_dir)
    config_hash, files = config_fingerprint(Path(config.config_dir))
    source = source_identity(project)
    stamp = started_at or datetime.now().astimezone().isoformat(timespec="seconds")
    identity = "|".join([
        __version__,
        str(source["git_sha"]),
        "dirty" if source["git_dirty"] else "clean",
        config_hash,
    ])
    return {
        "schema": 1,
        "version": __version__,
        "build_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
        **source,
        "config_dir": str(Path(config.config_dir)),
        "config_sha256": config_hash,
        "config_files": files,
        "started_at": stamp,
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "service": os.environ.get("XPC_SERVICE_NAME", ""),
        "context": context or {},
    }


def write_runtime_provenance(
    config: Any,
    *,
    context: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Atomically publish the current process identity under data/runtime/."""
    path = Path(config.data_dir) / "runtime" / "provenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = collect_provenance(config, context=context)
    partial = path.with_suffix(".json.tmp")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    partial.replace(path)
    return path, payload


def read_runtime_provenance(config: Any) -> dict[str, Any]:
    path = Path(config.data_dir) / "runtime" / "provenance.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict):
        return {"available": False, "reason": "provenance payload is not an object"}
    return {"available": True, "path": str(path), **payload}
