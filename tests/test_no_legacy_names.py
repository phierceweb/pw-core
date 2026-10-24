"""No pf-era name survives in tracked files (CHANGELOG history and listed exceptions aside)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = re.compile(
    r"\bpf_core\b|\bpf-core\b|\bPF_[A-Z]|\bpf-(guards|doctor|setup|jobs|eval|export)\b"
    r"|pf_guards|\.pf-guards\.toml|\bpf_(schema|tables|connection|engine|app_client|budget_disabled|app|engine_teardown)\b|\bpf_core_|\bpf_test_"
)
# (path, substring that is allowed to contain a legacy name)
ALLOW: tuple[tuple[str, str], ...] = (
    ("README.md", "published as pf-core"),
    ("src/pw_core/docs/INSTALLATION.md", "published as pf-core"),
    ("src/pw_core/_env_compat.py", "PF_"),
    ("src/pw_core/docs/config.md", "PF_"),
    ("src/pw_core/docs/doctor.md", "PF_"),
    ("src/pw_core/jobs/workers.py", "PF_"),
    ("tests/test_doctor.py", "PF_"),
    ("tests/test_env_compat.py", "PF_"),
    ("tests/test_jobs_workers.py", "PF_"),
    ("tests/test_packaging.py", "pf_core"),
    ("tests/test_no_legacy_names.py", ""),
)
SKIP = {"CHANGELOG.md"}


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    files = []
    for rel in out.stdout.split():
        p = ROOT / rel
        if rel in SKIP or not p.is_file():
            continue
        try:
            p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(p)
    return files


def _allowed(rel: str, line: str) -> bool:
    return any(rel == path and (not needle or needle in line) for path, needle in ALLOW)


def test_no_legacy_pf_names_in_tracked_files() -> None:
    hits = []
    for p in _tracked_text_files():
        rel = p.relative_to(ROOT).as_posix()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if LEGACY.search(line) and not _allowed(rel, line):
                hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not hits, "legacy pf-era names:\n" + "\n".join(hits)


def _tracked_symlinks() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-s"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [
        ROOT / line.split("\t", 1)[1]
        for line in out.stdout.splitlines()
        if line.startswith("120000 ")
    ]


def test_tracked_symlinks_resolve_and_carry_no_legacy_names() -> None:
    problems = []
    for link in _tracked_symlinks():
        target = os.readlink(link)
        rel = link.relative_to(ROOT).as_posix()
        if LEGACY.search(target):
            problems.append(f"{rel} -> {target}: legacy name in target")
        if not link.exists():
            problems.append(f"{rel} -> {target}: dangling")
    assert not problems, "\n".join(problems)
