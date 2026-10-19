"""The built wheel ships exactly the source tree — built the way CI builds it, through the sdist."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pw_core"


def _tree_members() -> set[str]:
    return {
        p.relative_to(ROOT / "src").as_posix()
        for p in SRC.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def test_wheel_built_through_the_sdist_matches_the_source_tree(tmp_path):
    """A straight ``--wheel`` build picks up stale ``build/lib`` leftovers; the sdist path is
    what publish.yml runs, so that is the artifact to compare."""
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    [whl] = tmp_path.glob("pw_core-*.whl")
    with zipfile.ZipFile(whl) as zf:
        names = set(zf.namelist())
        entry = next(n for n in names if n.endswith("entry_points.txt"))
        scripts = zf.read(entry).decode()
    shipped = {n for n in names if n.startswith("pw_core/")}
    assert shipped == _tree_members()
    assert all(s in scripts for s in ("pw-jobs", "pw-eval", "pw-export", "pytest11"))
    assert "pf_core" not in " ".join(names)
