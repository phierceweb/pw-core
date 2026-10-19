"""Tests for bin/new-consumer — the consumer scaffold generator.

Generates projects into tmp dirs and asserts they are conformant AND runnable
(the day-1 slice actually executes), so the scaffold can't silently rot.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PW_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = PW_ROOT / "bin" / "new-consumer"


def _load_generator():
    # bin/new-consumer is extensionless, so use SourceFileLoader directly.
    loader = importlib.machinery.SourceFileLoader("_pw_new_consumer", str(GENERATOR))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _run(argv: list[str]) -> None:
    assert _load_generator().main(argv) == 0


def _no_unsubstituted_tokens(root: Path) -> None:
    for f in root.rglob("*"):
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore")
            assert "__PKG__" not in text, f"unsubstituted __PKG__ in {f}"
            assert "__NAME__" not in text, f"unsubstituted __NAME__ in {f}"
            assert "__EXTRAS__" not in text, f"unsubstituted __EXTRAS__ in {f}"
            assert "__DEPS__" not in text, f"unsubstituted __DEPS__ in {f}"
            assert "__SIBLINGS__" not in text, f"unsubstituted __SIBLINGS__ in {f}"
            assert "__DOCS_LINKS__" not in text, f"unsubstituted __DOCS_LINKS__ in {f}"


@pytest.mark.parametrize("layout,extras_token", [("lib", "pw-core[cli]"), ("app", "pw-core[full]")])
def test_scaffold_structure(tmp_path, layout, extras_token):
    gen = _load_generator()
    rc = gen.main(["demo-proj", "--layout", layout, "--dest", str(tmp_path)])
    assert rc == 0
    proj = tmp_path / "demo-proj"

    assert (proj / "pyproject.toml").is_file()
    assert (proj / "bin" / "run").is_file()
    assert (proj / "bin" / "setup").is_file()
    assert os.access(proj / "bin" / "run", os.X_OK), "bin/run must be executable"
    # the starter rules arrive from pw-setup at bin/setup; the scaffold leaves the dir
    assert (proj / ".ai" / "rules").is_dir()
    assert (proj / ".ai" / "plans" / ".gitkeep").is_file()

    meta = tomllib.loads((proj / "pyproject.toml").read_text())
    assert meta["project"]["name"] == "demo-proj"
    assert any(extras_token in d for d in meta["project"]["dependencies"])

    # Enforcement is stamped, not optional: gate config + pre-commit + CI backstop.
    expected_root = "app" if layout == "app" else "src/demo_proj"
    guards_cfg = tomllib.loads((proj / ".pw-guards.toml").read_text())
    assert guards_cfg["tool"]["pw_guards"]["root"] == expected_root
    assert "pw_guards" not in meta.get("tool", {}), "gate config must not be in pyproject.toml"
    assert (proj / ".pre-commit-config.yaml").is_file()
    assert (proj / ".github" / "workflows" / "guards.yml").is_file()

    _no_unsubstituted_tokens(proj)


def test_lib_package_path_substituted(tmp_path):
    gen = _load_generator()
    gen.main(["my-tool", "--layout", "lib", "--dest", str(tmp_path)])
    proj = tmp_path / "my-tool"
    # __PKG__ in the path became my_tool
    assert (proj / "src" / "my_tool" / "cli.py").is_file()
    assert not (proj / "src" / "__PKG__").exists()


def test_refuses_nonempty_dest(tmp_path):
    gen = _load_generator()
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "x").write_text("existing")
    with pytest.raises(SystemExit):
        gen.main(["taken", "--layout", "lib", "--dest", str(tmp_path)])


def test_custom_extras(tmp_path):
    gen = _load_generator()
    gen.main(["x-proj", "--layout", "lib", "--extras", "llm,redis", "--dest", str(tmp_path)])
    meta = tomllib.loads((tmp_path / "x-proj" / "pyproject.toml").read_text())
    assert any("pw-core[llm,redis]" in d for d in meta["project"]["dependencies"])


def _fake_template_root(tmp_path: Path) -> Path:
    """A minimal PW_ROOT whose template carries tool-cache junk."""
    root = tmp_path / "fakeroot"
    tpl = root / "templates" / "consumer-lib"
    (tpl / "src" / "__PKG__").mkdir(parents=True)
    (tpl / "src" / "__PKG__" / "cli.py").write_text("PKG = '__PKG__'\n")
    (tpl / "pyproject.toml").write_text('name = "__NAME__"\n')
    (root / "pyproject.toml").write_text('[project]\nname = "pw-core"\nversion = "0.0.0"\n')
    for cache in (".ruff_cache", ".pytest_cache", ".mypy_cache", "__pycache__"):
        d = tpl / "src" / "__PKG__" / cache
        d.mkdir()
        # Non-UTF-8: a stamp that tries to read it raises rather than mangling.
        d.joinpath("blob.bin").write_bytes(b"\xff\xfe\x00binary")
    (root / ".ai" / "rules").mkdir(parents=True)
    return root


def test_tool_cache_dirs_are_not_stamped(tmp_path, monkeypatch):
    """Tool caches in the template must not reach the scaffold — their binary
    contents break the UTF-8 stamp."""
    gen = _load_generator()
    monkeypatch.setattr(gen, "PW_ROOT", _fake_template_root(tmp_path))
    assert gen.main(["demo-proj", "--layout", "lib", "--dest", str(tmp_path / "out")]) == 0
    proj = tmp_path / "out" / "demo-proj"
    caches = {".ruff_cache", ".pytest_cache", ".mypy_cache", "__pycache__"}
    leaked = [str(p.relative_to(proj)) for p in proj.rglob("*") if caches & set(p.parts)]
    assert leaked == [], f"tool cache files copied into the scaffold: {leaked}"


def test_scaffolded_lib_cli_actually_runs(tmp_path):
    """The day-1 slice executes: generate, import the package, run its CLI."""
    gen = _load_generator()
    gen.main(["runnable-demo", "--layout", "lib", "--dest", str(tmp_path)])
    src = str(tmp_path / "runnable-demo" / "src")
    sys.path.insert(0, src)
    try:
        for m in [k for k in sys.modules if k.startswith("runnable_demo")]:
            del sys.modules[m]
        from typer.testing import CliRunner

        cli = importlib.import_module("runnable_demo.cli")
        result = CliRunner().invoke(cli.app, ["hello", "Ada"])
        assert result.exit_code == 0
        assert "hello, Ada" in result.output
    finally:
        sys.path.remove(src)
        for m in [k for k in sys.modules if k.startswith("runnable_demo")]:
            del sys.modules[m]


def _fake_console_script(proj: Path, name: str, marker: str) -> None:
    vbin = proj / ".venv" / "bin"
    vbin.mkdir(parents=True, exist_ok=True)
    script = vbin / name
    script.write_text(f"#!/bin/sh\necho {marker}\n")
    script.chmod(0o755)


@pytest.mark.parametrize("layout", ["lib", "app"])
def test_scaffolded_run_dispatches_venv_console_scripts(tmp_path, layout):
    """docs/doctor.md tells consumers to run `bin/run pw-doctor`; it has to reach the venv."""
    gen = _load_generator()
    gen.main(["dispatch-demo", "--layout", layout, "--dest", str(tmp_path)])
    proj = tmp_path / "dispatch-demo"
    _fake_console_script(proj, "pw-doctor", "RAN_PW_DOCTOR")

    out = subprocess.run([str(proj / "bin" / "run"), "pw-doctor"], capture_output=True, text=True)
    assert "RAN_PW_DOCTOR" in out.stdout


def test_scaffolded_run_venv_dispatch_is_pw_prefixed_only(tmp_path):
    """A venv console script must never hijack a command name the app owns."""
    gen = _load_generator()
    gen.main(["shadow-demo", "--layout", "lib", "--dest", str(tmp_path)])
    proj = tmp_path / "shadow-demo"
    _fake_console_script(proj, "hello", "VENV_HELLO_RAN")

    out = subprocess.run([str(proj / "bin" / "run"), "hello"], capture_output=True, text=True)
    assert "VENV_HELLO_RAN" not in out.stdout


def test_scaffolded_app_serves_index(tmp_path):
    """The app day-1 slice boots: generate, import the app, hit the index route."""
    gen = _load_generator()
    gen.main(["webdemo", "--layout", "app", "--dest", str(tmp_path)])
    root = str(tmp_path / "webdemo")
    sys.path.insert(0, root)

    def _purge():
        for m in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            del sys.modules[m]

    _purge()
    sys.path.remove(root)
    sys.path.insert(0, root)
    try:
        from starlette.testclient import TestClient

        app_mod = importlib.import_module("app")
        resp = TestClient(app_mod.app).get("/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
    finally:
        sys.path.remove(root)
        _purge()


def _deps(project: Path) -> list[str]:
    return tomllib.loads((project / "pyproject.toml").read_text())["project"]["dependencies"]


def test_base_only_scaffold_pins_only_pw_foundation(tmp_path):
    _run(["my-tool", "--layout", "lib", "--base-only", "--dest", str(tmp_path)])
    assert _deps(tmp_path / "my-tool") == ["pw-foundation~=1.0"]


def test_default_lib_scaffold_pins_both_packages(tmp_path):
    _run(["my-tool", "--layout", "lib", "--dest", str(tmp_path)])
    assert _deps(tmp_path / "my-tool") == [
        "pw-foundation~=1.0",
        f"pw-core[cli]~={_pyproject_version()}",
    ]


def test_base_only_is_for_the_lib_layout(tmp_path):
    with pytest.raises(SystemExit):
        _load_generator().main(
            ["my-app", "--layout", "app", "--base-only", "--dest", str(tmp_path)]
        )
    assert not (tmp_path / "my-app").exists()


def test_base_only_scaffold_never_names_pw_core(tmp_path):
    """A foundation-only project imports pw_foundation and depends on nothing else."""
    _run(["base-demo", "--layout", "lib", "--base-only", "--dest", str(tmp_path)])
    proj = tmp_path / "base-demo"
    _no_unsubstituted_tokens(proj)
    hits = [
        str(f.relative_to(proj))
        for f in proj.rglob("*")
        if f.is_file()
        and ("pw_core" in f.read_text(errors="ignore") or "pw-core" in f.read_text(errors="ignore"))
    ]
    assert hits == []
    assert "from pw_foundation.log import" in (proj / "src" / "base_demo" / "cli.py").read_text()
    assert (
        "from pw_foundation.config import AppConfig"
        in (proj / "src" / "base_demo" / "config.py").read_text()
    )


def test_base_only_cli_runs(tmp_path):
    """The argparse day-1 slice executes against pw-foundation alone."""
    pytest.importorskip("pw_foundation")
    _run(["base-demo", "--layout", "lib", "--base-only", "--dest", str(tmp_path)])
    proj = tmp_path / "base-demo"
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from base_demo.cli import main; raise SystemExit(main(['hello', 'Ada']))",
        ],
        capture_output=True,
        text=True,
        cwd=proj,
        env={**os.environ, "PYTHONPATH": str(proj / "src")},
    )
    assert out.returncode == 0, out.stderr
    assert "hello, Ada" in out.stdout


def _pyproject_version() -> str:
    import tomllib

    return tomllib.loads((PW_ROOT / "pyproject.toml").read_text())["project"]["version"]


def _scaffold_pytest(proj: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        capture_output=True,
        text=True,
        cwd=proj,
        env={**os.environ, "PYTHONPATH": str(proj / "src")},
    )


def test_core_pin_tracks_the_checkout_version():
    """The scaffold pins the version this checkout is, so a bump cannot leave it behind."""
    gen = _load_generator()
    assert gen.core_pin("cli") == f"pw-core[cli]~={_pyproject_version()}"
    assert "0.27" not in GENERATOR.read_text()


def test_base_only_scaffold_passes_its_own_tests(tmp_path):
    pytest.importorskip("pw_foundation")
    _run(["base-demo", "--layout", "lib", "--base-only", "--dest", str(tmp_path)])
    out = _scaffold_pytest(tmp_path / "base-demo")
    assert out.returncode == 0, out.stdout + out.stderr


def test_default_lib_scaffold_passes_its_own_tests(tmp_path):
    pytest.importorskip("typer")
    _run(["core-demo", "--layout", "lib", "--dest", str(tmp_path)])
    out = _scaffold_pytest(tmp_path / "core-demo")
    assert out.returncode == 0, out.stdout + out.stderr


def test_base_only_prose_makes_no_pw_core_claims(tmp_path):
    """Nothing the base-only scaffold says is borrowed pw-core wording."""
    _run(["base-demo", "--layout", "lib", "--base-only", "--dest", str(tmp_path)])
    proj = tmp_path / "base-demo"
    false_claims = (
        "pw-foundation[",
        "create_cli",
        "LLM clients",
        "patch releases",
        "project-structure.md",
    )
    hits = [
        f"{f.relative_to(proj)}: {claim}"
        for f in proj.rglob("*")
        if f.is_file()
        for claim in false_claims
        if claim in f.read_text(errors="ignore")
    ]
    assert hits == []


@pytest.mark.parametrize("layout", ["lib", "app"])
def test_scaffold_claude_md_describes_both_packages(tmp_path, layout):
    _run(["demo", "--layout", layout, "--dest", str(tmp_path)])
    text = (tmp_path / "demo" / "CLAUDE.md").read_text()
    assert "docs/pw-foundation/modules.md" in text
    assert "docs/pw-core/modules.md" in text
    assert "pw_foundation." in text
    assert "Import from `pw_core.*`" not in text


@pytest.mark.parametrize("argv", [["lib"], ["app"], ["lib", "--base-only"]])
def test_scaffold_doc_pointers_resolve(tmp_path, argv):
    """Every docs/pw-foundation/x.md or docs/pw-core/x.md a scaffold cites is a doc that ships."""
    import pw_foundation

    _run(["demo", "--layout", *argv, "--dest", str(tmp_path)])
    proj = tmp_path / "demo"
    shipped = {
        "pw-foundation": Path(pw_foundation.__file__).parent / "docs",
        "pw-core": PW_ROOT / "src" / "pw_core" / "docs",
    }
    dead = []
    for f in proj.rglob("*"):
        if not f.is_file():
            continue
        for pkg, doc in re.findall(
            r"docs/(pw-foundation|pw-core)/([\w-]+\.md)", f.read_text(errors="ignore")
        ):
            if not (shipped[pkg] / doc).is_file():
                dead.append(f"{f.relative_to(proj)}: docs/{pkg}/{doc}")
    assert dead == []


@pytest.mark.parametrize(
    "argv,siblings",
    [
        (["lib"], ["pw-foundation", "pw-core"]),
        (["app"], ["pw-foundation", "pw-core"]),
        (["lib", "--base-only"], ["pw-foundation"]),
    ],
)
def test_scaffold_setup_prefers_sibling_checkouts_and_leaves_wiring_to_pw_setup(
    tmp_path, argv, siblings
):
    _run(["demo", "--layout", *argv, "--dest", str(tmp_path)])
    proj = tmp_path / "demo"
    setup = (proj / "bin" / "setup").read_text()
    assert f"for sib in {' '.join(siblings)}; do" in setup
    assert 'rm -rf "' not in setup and "ln -s" not in setup  # no hand-rolled wiring beside pw-setup
    ignored = (proj / ".gitignore").read_text().splitlines()
    assert [line for line in ignored if line.startswith("docs/")] == [f"docs/{s}" for s in siblings]
