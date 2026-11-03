"""Docs drift gates: version examples in public prose must track released state."""

import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PIN_RE = re.compile(r"pw-core(?:\[[^\]]*\])?\s*(~=|>=|==)\s*(\d+)\.(\d+)(\.\d+)?")
FINAL_ZERO_MINOR = 28  # the 0.x line closed at v0.28.0

# 0.x tokens: v0.M, pre-0.M, 0.M.P. Bare two-part decimals (a 0.85 score) are not versions.
_VERSIONISH_RE = re.compile(
    r"(?<![\w.])v0\.(\d{1,3})(?!\d)"
    r"|(?<![\w.])pre-0\.(\d{1,3})(?!\d)"
    r"|(?<![\d.])0\.(\d{1,3})\.\d"
)
# Real dependency versions share these shapes; a match is exempt when one of these
# names sits directly before it (``httpx>=0.28``, ``pw-foundation 1.0.1``).
_DEP_VERSIONS = (
    "ruff",
    "httpx",
    "uvicorn",
    "anthropic",
    "fastapi",
    "json-repair",
    "typer",
    "slowapi",
    "alembic",
    "pw-foundation",
    "semconv",
    "semconv version",
    "semantic-conventions",
    "opentelemetry.io/schemas",
)


_DEP_BEFORE = re.compile(
    r"(?<![\w-])(?:" + "|".join(re.escape(d) for d in _DEP_VERSIONS) + r")(?:\[[^\]]*\])?\W{0,3}$"
)


def _offending_starts(
    line: str, zero_minor: int, major: int, minor: int, current: bool
) -> list[int]:
    """Start offsets of version tokens ahead of the released line, dependency versions excluded."""
    starts = [
        m.start()
        for m in _VERSIONISH_RE.finditer(line)
        if _offending_minors(m.group(0), zero_minor)
    ]
    if current and major:
        starts += [
            m.start()
            for m in _current_line_re(major).finditer(line)
            if _offending_current_line(m.group(0), major, minor)
        ]
    return [s for s in starts if not _DEP_BEFORE.search(line[:s])]


def _zero_line_headings() -> list[int]:
    return [
        int(m) for m in re.findall(r"^## v0\.(\d+)\.\d+", (ROOT / "CHANGELOG.md").read_text(), re.M)
    ]


def _current_line_re(major: int) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.])v{major}\.(\d{{1,3}})(?!\d)|(?<![\d.]){major}\.(\d{{1,3}})\.\d")


def _offending_current_line(line: str, major: int, minor: int) -> list[int]:
    """Minors on the current ``major`` line (``vX.M``, ``X.M.P``) ahead of the released one."""
    if major == 0:
        return []
    found = [
        int(g) for m in _current_line_re(major).finditer(line) for g in m.groups() if g is not None
    ]
    return [m for m in found if m > minor]


def _current_version() -> tuple[int, int]:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    major, minor = version.split(".")[:2]
    return int(major), int(minor)


def _offending_minors(line: str, released_minor: int) -> list[int]:
    minors = [int(g) for m in _VERSIONISH_RE.finditer(line) for g in m.groups() if g is not None]
    return [m for m in minors if m > released_minor]


def _text(path: Path) -> str:
    try:
        return path.read_text()
    except (UnicodeDecodeError, OSError):
        return ""


def _pin_example_files() -> list[Path]:
    return [
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        *sorted((ROOT / "src/pw_core/rules").glob("*.md")),
        *sorted(p for p in (ROOT / "bin").iterdir() if p.is_file() and not p.name.startswith(".")),
        *sorted((ROOT / "src/pw_core/docs").rglob("*.md")),
        *sorted((ROOT / ".ai/rules").glob("*.md")),
        *sorted(ROOT.glob("templates/*/pyproject.toml")),
    ]


def test_pin_examples_track_current_minor():
    """Patch fixes land on the newest minor only, so a stale ``~=`` example
    points consumers at a frozen line."""
    major, minor = _current_version()
    offenders = []
    for path in _pin_example_files():
        for lineno, line in enumerate(_text(path).splitlines(), 1):
            for m in PIN_RE.finditer(line):
                op, pinned, patch = m.group(1), (int(m.group(2)), int(m.group(3))), m.group(4)
                # On 0.x a pin holds the minor (~=0.M.P); from 1.0 it holds only the
                # major (~=X.0), so a three-part pin would shut out later minors.
                if op != "~=":
                    ok = pinned[0] == major and pinned <= (major, minor)
                elif major == 0:
                    ok = pinned == (major, minor)
                else:
                    ok = not patch and pinned == (major, 0)
                if not ok:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {m.group(0)}")
    assert not offenders, (
        f"pw-core pin examples not on the current line ({major}.{minor}):\n" + "\n".join(offenders)
    )


def test_prepub_detector_semantics():
    """Gate logic pinned against synthetic tokens (built, not literal, so the
    fingerprint gate never matches this file)."""
    cur = 7
    high, low = cur + 35, cur - 2
    assert _offending_minors(f"see v0.{high} for details", cur) == [high]
    assert _offending_minors(f"moved out in 0.{high}.0", cur) == [high]
    assert _offending_minors(f"preserves pre-0.{cur + 6} behavior", cur) == [cur + 6]
    assert _offending_minors(f"v0.{cur + 1}", cur) == [cur + 1]
    assert _offending_minors(f"shipped in v0.{low}.3 and 0.{low}.1", cur) == []
    assert _offending_minors("score 0.85, temperature 0.2", cur) == []
    # The same token becomes legal the moment that minor is released.
    assert _offending_minors(f"released 0.{cur + 1}.0", cur + 1) == []


def test_current_line_detector_semantics():
    nxt = 2
    assert _offending_current_line(f"see v1.{nxt} for details", 1, 0) == [nxt]
    assert _offending_current_line(f"added in 1.{nxt}.0", 1, 0) == [nxt]
    assert _offending_current_line("shipped in 1.0.0, see v1.0", 1, 0) == []
    assert _offending_current_line(f"released 1.{nxt}.0", 1, nxt) == []
    assert _offending_current_line(f"v0.{nxt + 40}", 0, 5) == []


def test_dependency_versions_are_exempt_only_when_adjacent():
    nxt, high = 2, FINAL_ZERO_MINOR + 1
    assert _offending_starts(f"pw-core v1.{nxt} adds a command", high - 1, 1, 0, current=True)
    assert _offending_starts(f"pw-core 1.{nxt}.0 with pw-foundation", high - 1, 1, 0, current=True)
    assert _offending_starts(f"a scruffy 1.{nxt}.0 release", high - 1, 1, 0, current=True)
    assert not _offending_starts(f"needs pw-foundation 1.{nxt}.0", high - 1, 1, 0, current=True)
    assert not _offending_starts(f"uvicorn[standard]>=0.{high}.0", high - 1, 1, 0, current=True)


def test_final_zero_minor_is_the_last_zero_line_heading():
    assert max(_zero_line_headings()) == FINAL_ZERO_MINOR


def test_no_prepublication_version_references():
    """The shipped tree must not reference versions ahead of the released
    line. Scans prose, source, scripts, workflows, and templates; skips this
    file (it manufactures version tokens) and dependency versions."""
    major, minor = _current_version()
    zero_line_minor = minor if major == 0 else FINAL_ZERO_MINOR
    scan = [
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CHANGELOG.md",
        ROOT / "pyproject.toml",
        *sorted((ROOT / "src/pw_core").rglob("*.md")),
        *sorted((ROOT / "src/pw_core").rglob("*.py")),
        *sorted((ROOT / "tests").rglob("*.py")),
        *sorted((ROOT / "bin").iterdir()),
        *sorted((ROOT / ".github").rglob("*")),
        *sorted((ROOT / ".ai/rules").glob("*.md")),
        *sorted((ROOT / "templates").rglob("*")),
    ]
    self_path = Path(__file__).resolve()
    offenders = []
    for path in scan:
        if not path.is_file() or path.resolve() == self_path:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        # Test fixtures use arbitrary 1.x versions and IP addresses.
        in_tests = path.is_relative_to(ROOT / "tests")
        for lineno, line in enumerate(text.splitlines(), 1):
            if not _offending_starts(line, zero_line_minor, major, minor, current=not in_tests):
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "version references ahead of the released line (pre-publication/future numbering):\n"
        + "\n".join(offenders)
    )


def test_no_backup_or_debris_files_are_tracked():
    """No editor backup or debris file is tracked.

    Enumerated from git, not globbed: ``*.md`` does not match ``.md.bak``.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    debris = [
        f
        for f in tracked
        if f.endswith((".bak", ".orig", ".rej", ".tmp", ".swp", "~")) or Path(f).name == ".DS_Store"
    ]
    assert not debris, f"debris committed: {debris}"


def _requires_python() -> tuple[int, int]:
    spec = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    m = re.search(r">=\s*(\d+)\.(\d+)", spec)
    assert m, f"cannot parse requires-python: {spec!r}"
    return int(m.group(1)), int(m.group(2))


def test_templates_track_requires_python():
    """A scaffolded project that claims a lower floor than the pw-core it pins
    is unresolvable on the interpreters it advertises."""
    ours = _requires_python()
    offenders = []
    for path in sorted(ROOT.glob("templates/*/pyproject.toml")):
        spec = tomllib.loads(path.read_text())["project"]["requires-python"]
        m = re.search(r">=\s*(\d+)\.(\d+)", spec)
        if not m or (int(m.group(1)), int(m.group(2))) != ours:
            offenders.append(f"{path.relative_to(ROOT)}: {spec}")
    assert not offenders, (
        f"template requires-python does not match pw-core's (>={ours[0]}.{ours[1]}):\n"
        + "\n".join(offenders)
    )


def _setup_scripts() -> list[Path]:
    """Every script that picks an interpreter or validates a venv — pw-core's own
    plus the copies ``bin/new-consumer`` stamps into each scaffolded project."""
    return [
        ROOT / "bin" / "setup-common",
        ROOT / "bin" / "verify-bare-install",
        *sorted(ROOT.glob("templates/*/bin/setup")),
    ]


def test_setup_scripts_track_requires_python():
    """The setup helpers build the venv, so a stale floor there produces an
    interpreter that cannot install the package it was created for. Covers the
    template copies too: they are what a scaffolded project runs on day one."""
    major, minor = _requires_python()
    wanted = f"({major}, {minor})"
    stale_interpreters = re.compile(rf"python{major}\.(\d+)\b")
    offenders = []
    for path in _setup_scripts():
        rel = path.relative_to(ROOT)
        text = path.read_text()
        found = set(re.findall(r"sys\.version_info >= (\(\d+, \d+\))", text))
        if found != {wanted}:
            offenders.append(f"{rel}: version_info {sorted(found) or 'check missing'} != {wanted}")
        stale = sorted({int(m) for m in stale_interpreters.findall(text) if int(m) < minor})
        if stale:
            offenders.append(f"{rel}: offers {major}.{stale} below the floor")
    assert not offenders, f"setup scripts do not enforce the package floor {wanted}:\n" + "\n".join(
        offenders
    )


def test_ruff_target_version_tracks_requires_python():
    """``target-version`` tells ruff which syntax to accept. Below the floor it
    silently rejects language features the project is entitled to use."""
    major, minor = _requires_python()
    wanted = f"py{major}{minor}"
    offenders = []
    for path in [ROOT / "pyproject.toml", *sorted(ROOT.glob("templates/*/pyproject.toml"))]:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            m = re.match(r'\s*target-version\s*=\s*"([^"]+)"', line)
            if m and m.group(1) != wanted:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {m.group(1)} != {wanted}")
    assert not offenders, (
        f"ruff target-version does not match requires-python ({wanted}):\n" + "\n".join(offenders)
    )


_BASE_PIN_THREE_COMPONENTS = re.compile(r"pw-foundation\s*~=\s*1\.0\.\d")


def test_base_pin_examples_use_two_components():
    """``pw-foundation~=1.0`` admits every additive 1.x release; a third component
    (``~=1.0.0``) would pin consumers to 1.0.x patches and miss them."""
    offenders = []
    for path in _pin_example_files():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if _BASE_PIN_THREE_COMPONENTS.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "pw-foundation pins must be two components (~=1.0):\n" + "\n".join(
        offenders
    )
