#!/usr/bin/env python3
"""
Fail if anything that belongs only in the private operational workspace has
leaked into this public showcase repository.

    python tools/check_showcase_clean.py [root]

This is a guardrail, not a guarantee. It catches the known categories of
leak (licensed certification-vendor material, real student identifiers,
school-district contact details, live infrastructure, credentials, real
assessment passwords) by pattern. A human read-through before publishing is
still required. Runs in CI on every push.

Exit code 1 on any finding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Each rule: (name, compiled pattern, why it matters)
RULES = [
    ("licensed-vendor", re.compile(r"g\s*metrix|certi\s*port", re.I),
     "Licensed certification-prep material and vendor references stay in the private workspace."),
    ("vendor-exam-code", re.compile(r"\bINF-30\d\b|\bITS\s+OD\s+30\d\b", re.I),
     "Vendor exam/course codes point back to licensed material."),
    ("real-pseudonym-format", re.compile(r"\bPY\d-[A-Z]-[A-Z]+\d{2}\b"),
     "The real pseudonymous ID format is not published. Use STUDENT-0001 style IDs."),
    ("pseudonym-term", re.compile(r"\bcode\s?names?\b", re.I),
     "Use 'pseudonymous student ID' in public docs."),
    ("district-email", re.compile(r"@cps\.edu\b", re.I),
     "School-district email addresses are never published."),
    ("district-name", re.compile(r"chicago public schools|\bCPS\b"),
     "Keep the employer/district unnamed in the public showcase."),
    ("live-domain", re.compile(r"foxcs\.online", re.I),
     "Live student-facing infrastructure is not linked from the public showcase."),
    ("ipv4", re.compile(r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}\b"),
     "No server IP addresses."),
    ("hardcoded-password", re.compile(r"""(?:password|passwd|pwd)\w*\s*[:=]\s*['"][^'"]{4,}['"]""", re.I),
     "No credentials or quiz passwords, even old ones."),
    ("secret-token", re.compile(r"(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"),
     "No API keys or tokens."),
]

TEXT_SUFFIXES = {".py", ".md", ".json", ".html", ".csv", ".txt", ".yml", ".yaml", ".sh", ".toml", ".cfg", ".js", ".mjs"}
TEXT_NAMES = {".gitignore", ".gitattributes", "LICENSE"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "htmlcov"}
SKIP_FILES = {".coverage"}  # local test artifacts, gitignored


def scan(root: Path) -> list[tuple[Path, int, str, str]]:
    findings = []
    self_path = Path(__file__).resolve()
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts) or path.name in SKIP_FILES or not path.is_file():
            continue
        if path.resolve() == self_path:
            continue  # this file necessarily contains the patterns
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            findings.append((path, 0, "binary-file", f"Unexpected non-text file ({path.suffix or 'no extension'}). Review before publishing."))
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for name, pattern, why in RULES:
                if pattern.search(line):
                    findings.append((path, lineno, name, why))
    return findings


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    findings = scan(root)
    for path, lineno, name, why in findings:
        print(f"{path.relative_to(root)}:{lineno}: [{name}] {why}")
    if findings:
        print(f"\n{len(findings)} finding(s). Not safe to publish.")
        sys.exit(1)
    print("Clean: no known private-workspace patterns found.")


if __name__ == "__main__":
    main()
