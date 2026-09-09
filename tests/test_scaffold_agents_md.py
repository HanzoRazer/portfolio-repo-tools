"""SCAFFOLD-DIST-001 — self-test for scripts/scaffold_agents_md.py.

Every defect this scaffolder has had was an ENVIRONMENT-COUPLING bug: a worktree
directory name, a case-insensitive filesystem, a non-TTY stdin, an undetectable
default branch. None was visible by reading the code; each appeared only when the
tool met a real repository.

So these tests build **real temp git repos** and drive the real CLI as a
subprocess, asserting on OUTPUT -- files written, file contents, exit codes --
never on internal calls. Mocking the environment would test the model of the
environment, and a wrong model of it is the entire defect history.

Two families of test live here. The **seed tests** came from the original
self-test and concern environmental correctness, which survived the change of
product. The **contract tests** are new, and hold the narrower thing this tool
was actually authorized to do: workflow discipline only, refusing wherever an
existing authority might already own that ground.

The original suite's PR-template coverage is deliberately absent. That capability
was removed rather than disabled, so there is nothing to test -- and a test
asserting "the PR template is unchanged" would couple this tool to an authority
it has no business knowing exists.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCAFFOLDER = Path(__file__).resolve().parents[1] / "scripts" / "scaffold_agents_md.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _make_repo(
    root: Path,
    *,
    name: str = "repo",
    remote: str | None = None,
    branch: str = "main",
    origin_head: bool = True,
    files: dict[str, str] | None = None,
) -> Path:
    """A real git repo with one commit, and optionally a fake origin."""
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", branch)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    for rel, body in (files or {}).items():
        (repo / rel).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    if remote:
        _git(repo, "remote", "add", "origin", remote)
        if origin_head:
            # Simulate a resolvable origin/HEAD without a real network remote.
            _git(repo, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
            _git(
                repo,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                f"refs/remotes/origin/{branch}",
            )
    return repo


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    """Invoke the real CLI with stdin closed -- the agent-session condition."""
    return subprocess.run(
        [sys.executable, str(SCAFFOLDER), str(repo), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def _snapshot(repo: Path) -> dict[str, bytes]:
    """Every tracked-or-not file under the repo, excluding .git internals."""
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


# --------------------------------------------------------------------------- #
# Seed 1 — repository identity survives worktrees (defect #3)
# --------------------------------------------------------------------------- #


def test_heading_uses_remote_name_not_directory_name(tmp_path: Path) -> None:
    """A worktree directory is named for the task, not the repository.

    This produced a real wrong file: run from a worktree at C:/tmp/ltb-sprints,
    the heading read "Agent instructions - ltb-sprints".
    """
    repo = _make_repo(
        tmp_path, name="ltb-sprints", remote="https://example.com/org/luthiers-toolbox.git"
    )
    assert _run(repo).returncode == 0
    heading = (repo / "AGENTS.md").read_text(encoding="utf-8").splitlines()[0]
    assert heading == "# Agent instructions — luthiers-toolbox"
    assert "ltb-sprints" not in heading


@pytest.mark.parametrize(
    "remote",
    [
        "https://example.com/org/tap_tone_pi.git",
        "https://example.com/org/tap_tone_pi",
        "git@example.com:org/tap_tone_pi.git",
        "ssh://git@example.com/org/tap_tone_pi.git",
    ],
)
def test_identity_is_correct_for_every_common_remote_form(
    tmp_path: Path, remote: str
) -> None:
    """Identity right for HTTPS and wrong for SSH would fail where it matters."""
    repo = _make_repo(tmp_path, name=f"wt{abs(hash(remote)) % 9999}", remote=remote)
    assert _run(repo).returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "# Agent instructions — tap_tone_pi"


def test_no_remote_falls_back_to_directory_name_and_says_so(tmp_path: Path) -> None:
    """The fallback is legitimate but must never be silent.

    With no origin there is no better identity available -- but the directory
    name is exactly what produced defect #3, so the report has to surface it.
    """
    repo = _make_repo(tmp_path, name="some-checkout", remote=None, branch="main")
    result = _run(repo)
    assert result.returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "# Agent instructions — some-checkout"
    assert "directory name" in result.stdout
    assert "no origin remote" in result.stdout


def test_repo_name_override_beats_the_fallback(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="some-checkout", remote=None, branch="main")
    assert _run(repo, "--repo-name", "real-name").returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "# Agent instructions — real-name"


# --------------------------------------------------------------------------- #
# Seed 2 — non-TTY --force (defect #1)
# --------------------------------------------------------------------------- #


def test_force_without_yes_refuses_headlessly_and_writes_nothing(tmp_path: Path) -> None:
    """The original crashed with EOFError AFTER writing the first file.

    A half-completed overwrite is worse than a refusal, so the guard must fire
    before anything is written -- asserted by content, not just exit code.
    """
    repo = _make_repo(tmp_path, name="r3a", remote="https://example.com/o/r3a.git")
    (repo / "AGENTS.md").write_text("ORIGINAL\n", encoding="utf-8")
    before = _snapshot(repo)

    result = _run(repo, "--force")
    assert result.returncode != 0
    assert "EOFError" not in result.stderr and "Traceback" not in result.stderr
    assert _snapshot(repo) == before


def test_force_with_yes_overwrites_headlessly(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r3b", remote="https://example.com/o/r3b.git")
    (repo / "AGENTS.md").write_text("ORIGINAL\n", encoding="utf-8")

    result = _run(repo, "--force", "--yes")
    assert result.returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "ORIGINAL" not in body
    assert body.startswith("# Agent instructions — r3b")


# --------------------------------------------------------------------------- #
# Seed 3 — an undetectable default must never be written silently
# --------------------------------------------------------------------------- #


def test_undetectable_default_branch_refuses_headlessly(tmp_path: Path) -> None:
    """THE POISON-PILL GUARD.

    Repo on 'develop', no origin/HEAD, no local main/master. Writing here would
    put "branch from main" into a canonical file naming a branch that is not this
    repo's default -- and replicate that across every repo scaffolded the same
    way. It must refuse, and it must write nothing.
    """
    repo = _make_repo(tmp_path, name="r4", branch="develop", remote=None)

    result = _run(repo)
    assert result.returncode != 0, "wrote on a guessed default branch"
    assert not (repo / "AGENTS.md").exists(), "wrote a file it could not name correctly"
    assert "could not detect the default branch" in result.stderr
    assert "--default-branch" in result.stderr


def test_default_branch_override_is_used_throughout(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r4b", branch="develop", remote=None)

    result = _run(repo, "--default-branch", "develop")
    assert result.returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Branch from current `develop`. Always." in body
    assert "origin/develop" in body
    # The self-check must assert against develop, not a stray 'main'.
    assert "git merge-base HEAD origin/develop" in body
    assert "origin/main" not in body


# --------------------------------------------------------------------------- #
# Seed 4 — happy path, never-overwrite, non-git
# --------------------------------------------------------------------------- #


def test_happy_path_writes_the_workflow_entry_point(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r5", remote="https://example.com/o/r5.git")

    result = _run(repo)
    assert result.returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "WRITE" in result.stdout
    assert "## Branch from current `main`. Always." in body
    assert "## Self-check before opening a pull request" in body


def test_second_run_does_not_clobber(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r5b", remote="https://example.com/o/r5b.git")
    assert _run(repo).returncode == 0
    (repo / "AGENTS.md").write_text("HAND EDITED\n", encoding="utf-8")

    result = _run(repo)
    assert result.returncode == 0
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == "HAND EDITED\n"
    assert "SKIP" in result.stdout


def test_refuses_a_non_git_directory(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    result = _run(plain)
    assert result.returncode == 2
    assert not (plain / "AGENTS.md").exists()


# --------------------------------------------------------------------------- #
# Contract 1 — authority collision fails closed
# --------------------------------------------------------------------------- #


def test_a_ttp_like_repository_is_refused_with_zero_mutation(tmp_path: Path) -> None:
    """The grounded case, and the reason the signal list includes CONTRIBUTING.

    In tap_tone_pi, CLAUDE.md is the project/measurement authority while
    CONTRIBUTING.md owns branch and pull-request workflow. A preflight checking
    only CLAUDE.md would see no workflow authority there and conclude a
    delegating AGENTS.md was permissible -- the wrong answer, reached by
    checking the wrong file.
    """
    repo = _make_repo(
        tmp_path,
        name="tap_tone_pi",
        remote="https://example.com/o/tap_tone_pi.git",
        files={
            "CLAUDE.md": "# Orientation\n\nMeasurement boundary.\n",
            "CONTRIBUTING.md": "# Contributing\n\n## Branching\n\n## Pull Requests\n",
        },
    )
    before = _snapshot(repo)

    result = _run(repo)
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert _snapshot(repo) == before
    assert "CLAUDE.md" in result.stderr and "CONTRIBUTING.md" in result.stderr
    assert "--authority-disposition" in result.stderr


def test_an_unrelated_contributing_still_refuses_automatically(tmp_path: Path) -> None:
    """No prose inference, in either direction.

    This CONTRIBUTING.md says nothing about branches. v1 must still refuse rather
    than concluding non-overlap from the text -- deciding it "obviously does not
    overlap" is the same semantic classification as deciding that it does, and
    reintroduces the judgment through a side door.
    """
    repo = _make_repo(
        tmp_path,
        name="r6b",
        remote="https://example.com/o/r6b.git",
        files={"CONTRIBUTING.md": "# Contributing\n\nBe kind. File issues.\n"},
    )
    before = _snapshot(repo)

    result = _run(repo)
    assert result.returncode != 0
    assert _snapshot(repo) == before
    assert "not proof that they overlap" in result.stderr


def test_operator_may_rule_that_existing_authority_covers_workflow(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        name="r6c",
        remote="https://example.com/o/r6c.git",
        files={"CONTRIBUTING.md": "# Contributing\n\n## Branching\n"},
    )
    before = _snapshot(repo)

    result = _run(repo, "--authority-disposition", "EXISTING_AUTHORITY_COVERS_WORKFLOW")
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert _snapshot(repo) == before


@pytest.mark.parametrize(
    "disposition", ["COEXISTENCE_EXPLICITLY_ALLOWED", "NO_OVERLAP_CONFIRMED"]
)
def test_approved_coexistence_delegates_rather_than_duplicating(
    tmp_path: Path, disposition: str
) -> None:
    """The CNC pattern: name the local authority, restate nothing it contains."""
    repo = _make_repo(
        tmp_path,
        name="r6d",
        remote="https://example.com/o/r6d.git",
        files={"CLAUDE.md": "# Project context\n\nArchitecture rules live here.\n"},
    )

    result = _run(repo, "--authority-disposition", disposition)
    assert result.returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "`CLAUDE.md`" in body
    assert "apply too" in body
    assert "restates nothing found there" in body
    # Reads correctly for one authority as well as several. A generated file
    # lands in repositories nobody proofreads it in.
    assert "`CLAUDE.md` carry" not in body
    # Delegation, not duplication: the other authority's content is not copied.
    assert "Architecture rules live here" not in body
    # And the local authority is untouched.
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").startswith("# Project context")


# --------------------------------------------------------------------------- #
# Contract 2 — the generated artifact stays narrow
# --------------------------------------------------------------------------- #


def test_no_incident_history_is_invented_or_stubbed(tmp_path: Path) -> None:
    """An incident that justified a rule elsewhere is evidence, not boilerplate.

    The original scaffolder emitted TODO blocks for incidents and verification
    gates. Even as placeholders those assert that every repository owes an
    incident history, so v1 emits neither the history nor the placeholder.
    """
    repo = _make_repo(tmp_path, name="r7", remote="https://example.com/o/r7.git")
    assert _run(repo).returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")

    assert "INCIDENTS" not in body
    assert "VERIFICATION GATES" not in body
    assert "TODO" not in body
    # Nor any other repository's specific history.
    for foreign in ("#12", "#13", "#14", "CNC Production Shop"):
        assert foreign not in body


def test_the_tool_has_no_pr_template_capability_at_all(tmp_path: Path) -> None:
    """Architectural absence, not a disabled feature.

    A future change adding PR-template generation should read as a scope
    expansion in the diff, not as flipping a flag on machinery already present.
    """
    repo = _make_repo(tmp_path, name="r7b", remote="https://example.com/o/r7b.git")
    assert _run(repo).returncode == 0

    assert not (repo / ".github").exists()
    assert list(repo.glob("**/pull_request_template.md")) == []
    source = SCAFFOLDER.read_text(encoding="utf-8")
    assert "pull_request_template" not in source
    assert "PULL_REQUEST_TEMPLATE" not in source


def test_the_artifact_is_workflow_only(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r7c", remote="https://example.com/o/r7c.git")
    assert _run(repo).returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")

    assert "## Branch from current" in body
    assert "## Self-check before opening a pull request" in body
    # It must not start inventing project or architecture instruction.
    for foreign in ("## Repository Structure", "## Architecture", "## Testing"):
        assert foreign not in body


# --------------------------------------------------------------------------- #
# Contract 3 — every refusal path leaves the repository untouched
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "case",
    [
        "undetectable_branch",
        "force_without_yes",
        "authority_unresolved",
        "authority_covers_workflow",
    ],
)
def test_every_refusal_path_mutates_nothing(tmp_path: Path, case: str) -> None:
    """Generalizes the EOFError-after-first-write defect beyond its one shape.

    The invariant is preflight-before-mutation, not the number of files written,
    so it is asserted against the whole repository rather than one path.
    """
    if case == "undetectable_branch":
        repo = _make_repo(tmp_path, name="z1", branch="develop", remote=None)
        args: tuple[str, ...] = ()
    elif case == "force_without_yes":
        repo = _make_repo(tmp_path, name="z2", remote="https://example.com/o/z2.git")
        (repo / "AGENTS.md").write_text("ORIGINAL\n", encoding="utf-8")
        args = ("--force",)
    elif case == "authority_unresolved":
        repo = _make_repo(
            tmp_path,
            name="z3",
            remote="https://example.com/o/z3.git",
            files={"CLAUDE.md": "# ctx\n"},
        )
        args = ()
    else:
        repo = _make_repo(
            tmp_path,
            name="z4",
            remote="https://example.com/o/z4.git",
            files={"CONTRIBUTING.md": "# c\n"},
        )
        args = ("--authority-disposition", "EXISTING_AUTHORITY_COVERS_WORKFLOW")

    before = _snapshot(repo)
    result = _run(repo, *args)

    assert result.returncode != 0, case
    assert _snapshot(repo) == before, case
    assert "Traceback" not in result.stderr, case
    assert result.stderr.startswith("refused:"), case
