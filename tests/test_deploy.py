"""Tests for the deploy script.

Deployment is the one step that can leave the user worse off than not running
it at all: the skill is registered by the presence of SKILL.md, so a partial
install produces a skill Claude will happily invoke and then fail inside.

Run with:  ./tests/run.sh
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "deploy.sh"


def run_deploy(dest, src=None, check=False):
    script = (Path(src) / "deploy.sh") if src else DEPLOY
    return subprocess.run(
        [str(script)], check=check, capture_output=True, text=True,
        env={**os.environ, "DEST_DIR": str(dest)},
    )


def test_deploy_ignores_pycache_left_by_the_test_run(tmp_path):
    """`cp scripts/*` choked on __pycache__, which running the tests creates.

    Because the script rm -rf'd the destination first, the failure destroyed a
    working install instead of merely declining to update it.
    """
    (REPO / "scripts" / "__pycache__").mkdir(exist_ok=True)
    dest = tmp_path / "skills" / "paper-summary"

    proc = run_deploy(dest)

    assert proc.returncode == 0, proc.stderr
    assert not (dest / "scripts" / "__pycache__").exists()


def test_deploy_installs_every_file_the_skill_needs(tmp_path):
    """A skill missing its domain guides fails at step 3, not at install time."""
    dest = tmp_path / "skills" / "paper-summary"

    run_deploy(dest, check=True)

    assert (dest / "SKILL.md").is_file()
    for name in ("ingest.py", "verify.py", "render_pdf.sh", "setup.sh"):
        assert (dest / "scripts" / name).is_file(), f"missing scripts/{name}"
    for name in ("ai.md", "general.md", "hep.md", "physics.md"):
        assert (dest / "domains" / name).is_file(), f"missing domains/{name}"
    for name in ("render_pdf.sh", "setup.sh"):
        assert os.access(dest / "scripts" / name, os.X_OK), f"{name} not executable"


def test_a_failed_deploy_leaves_the_previous_install_untouched(tmp_path):
    """Half-replacing an installed skill is worse than refusing to update it."""
    dest = tmp_path / "skills" / "paper-summary"
    (dest / "domains").mkdir(parents=True)
    (dest / "SKILL.md").write_text("PREVIOUS GOOD INSTALL")
    (dest / "domains" / "ai.md").write_text("PREVIOUS GOOD GUIDE")

    # A source tree that is missing SKILL.md, so the copy must fail partway.
    broken = tmp_path / "broken-src"
    broken.mkdir()
    shutil.copy(DEPLOY, broken / "deploy.sh")
    shutil.copytree(REPO / "scripts", broken / "scripts")
    shutil.copytree(REPO / "domains", broken / "domains")

    proc = run_deploy(dest, src=broken)

    assert proc.returncode != 0, "deploy should fail when SKILL.md is missing"
    assert (dest / "SKILL.md").read_text() == "PREVIOUS GOOD INSTALL"
    assert (dest / "domains" / "ai.md").read_text() == "PREVIOUS GOOD GUIDE"


def test_deploy_leaves_no_staging_directory_behind(tmp_path):
    """The staging copy lives next to the target; it must not accumulate."""
    dest = tmp_path / "skills" / "paper-summary"

    run_deploy(dest, check=True)

    siblings = [p.name for p in dest.parent.iterdir() if p.name != dest.name]
    assert siblings == [], f"staging leftovers: {siblings}"
