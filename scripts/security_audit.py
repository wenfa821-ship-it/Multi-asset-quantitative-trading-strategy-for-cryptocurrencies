#!/usr/bin/env python3
"""Fail closed when a credential is present in a tracked or reachable Git blob.

The audit has two complementary layers:

* exact values loaded from the local credential files (the strongest check);
* a small set of high-confidence token/private-key signatures for CI, where the
  credential files intentionally do not exist.

Findings never print the secret itself. Only the source label, path and line are
reported, so the command is safe to paste into release evidence.
"""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
MAX_BYTES = 10_000_000

ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"
)
SENSITIVE_NAME = re.compile(
    r"(KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|PRIVATE|WEBHOOK|COOKIE|RPC_URL)", re.I
)
PLACEHOLDER = re.compile(
    r"^(?:|changeme|example|placeholder|your[_-].*|<.*>|\$\{.*\})$", re.I
)
TOKEN_PATTERNS = {
    "private-key-block": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github-token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"
    ),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "openai-key": re.compile(r"\bsk-(?:proj-|live-)?[A-Za-z0-9_-]{20,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "jwt": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    ),
}


@dataclass(frozen=True)
class Finding:
    source: str
    path: str
    line: int


def _git(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def tracked_files() -> list[str]:
    raw = _git("ls-files", "-z").stdout
    return [path for path in raw.split("\0") if path]


def load_secret_values(paths: list[Path]) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            match = ASSIGNMENT.match(raw)
            if not match or not SENSITIVE_NAME.search(match.group(1)):
                continue
            value = match.group(2).strip()
            if " #" in value:
                value = value.split(" #", 1)[0].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if len(value) < 8 or PLACEHOLDER.match(value):
                continue
            found[f"{path.name}:{match.group(1)}"] = value
    return found


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(relative: str, text: str, exact_values: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    for label, value in exact_values.items():
        start = text.find(value)
        if start >= 0:
            findings.append(Finding(label, relative, _line_number(text, start)))
    for label, pattern in TOKEN_PATTERNS.items():
        match = pattern.search(text)
        if match:
            findings.append(Finding(label, relative, _line_number(text, match.start())))
    return findings


def scan_current(
    files: list[str], exact_values: dict[str, str]
) -> list[Finding]:
    findings: list[Finding] = []
    for relative in files:
        path = PROJECT / relative
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(relative, text, exact_values))
    return findings


def scan_history(exact_values: dict[str, str]) -> list[Finding]:
    """Find the exact live credential values anywhere in reachable history."""
    findings: list[Finding] = []
    for label, value in exact_values.items():
        # The value is passed as an argv item internally, never logged or printed.
        proc = _git("log", "--all", f"-S{value}", "--format=%H", "--name-only", "--")
        commits: set[str] = set()
        paths: set[str] = set()
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            if re.fullmatch(r"[0-9a-f]{40,64}", line):
                commits.add(line)
            else:
                paths.add(line)
        if commits:
            first_path = min(paths) if paths else "?"
            findings.append(
                Finding(
                    label,
                    f"reachable-history commits={len(commits)} files={len(paths)} "
                    f"first={first_path}",
                    0,
                )
            )
    return findings


def credential_mode_findings(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            continue
        if mode & 0o077:
            findings.append(Finding("credential-file-mode", path.name, 0))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="credential leak audit")
    parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        help="credential file to compare exactly; repeatable (default: .env and .env.live if present)",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="also search every reachable Git commit for exact local credential values",
    )
    args = parser.parse_args(argv)

    env_paths = (
        [Path(path) for path in args.env_file]
        if args.env_file
        else [path for path in (PROJECT / ".env", PROJECT / ".env.live") if path.exists()]
    )
    exact_values = load_secret_values(env_paths)
    findings = scan_current(tracked_files(), exact_values)
    if args.history and exact_values:
        findings.extend(scan_history(exact_values))
    findings.extend(credential_mode_findings(env_paths))

    # Deduplicate history rows: `git log` can name the same path more than once.
    unique = sorted(set(findings), key=lambda item: (item.source, item.path, item.line))
    print(
        f"tracked_files={len(tracked_files())} exact_credentials={len(exact_values)} "
        f"history={'on' if args.history else 'off'} findings={len(unique)}"
    )
    for finding in unique:
        location = f"{finding.path}:{finding.line}" if finding.line else finding.path
        print(f"  {finding.source}: {location}")
    if unique:
        print("credential audit FAILED; rotate the credential and remove it from published refs")
        return 1
    print("credential audit PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
