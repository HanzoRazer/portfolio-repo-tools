"""SCAFFOLD-DIST-002 — self-test for scripts/scaffold_agents_md.py.

Every defect this scaffolder has had was an ENVIRONMENT-COUPLING bug: a worktree
directory name, a case-insensitive filesystem, a non-TTY stdin, an undetectable
default branch. None was visible by reading the code; each appeared only when the
tool met a real repository.

So these tests build **real temp git repos** and drive the real CLI as a
subprocess, asserting on OUTPUT -- files written, file contents, exit codes --
never on internal calls. Mocking the environment would test the model of the
environment, and a wrong model of it is the entire defect history. The single
exception is the atomic-write failure test (T28), where the whole point is a
replacement failure that cannot be provoked from the outside; there, and only
there, ``os.replace`` is monkeypatched.

The reconciled contract this suite holds (G1-G8):

* remote selection is ``origin`` if present, else a sole non-origin remote, and
  a refusal when several remotes exist without an ``origin``;
* with no remote, identity is the **primary worktree** basename, never a linked
  worktree's directory name;
* the default branch comes from an explicit existing branch or the authoritative
  remote's symbolic HEAD -- never from guessing local ``main``/``master``;
* an ``EXISTING_AUTHORITY_COVERS_WORKFLOW`` disposition is a **successful no-op**,
  not a refusal;
* the target is written atomically, so a failed write leaves the original intact.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCAFFOLDER = Path(__file__).resolve().parents[1] / "scripts" / "scaffold_agents_md.py"


def _load_module():
    """Import the scaffolder as a module (for the one unit-level test, T28)."""
    spec = importlib.util.spec_from_file_location("scaffold_agents_md", SCAFFOLDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before executing so dataclass processing can resolve the module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def _add_remote(repo: Path, name: str, url: str, *, head_branch: str | None = None) -> None:
    """Add a non-origin remote, optionally with a resolvable symbolic HEAD."""
    _git(repo, "remote", "add", name, url)
    if head_branch:
        _git(repo, "update-ref", f"refs/remotes/{name}/{head_branch}", "HEAD")
        _git(
            repo,
            "symbolic-ref",
            f"refs/remotes/{name}/HEAD",
            f"refs/remotes/{name}/{head_branch}",
        )


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
# Identity / remote (T01-T08)
# --------------------------------------------------------------------------- #


def test_T01_linked_worktree_name_is_not_repository_identity(tmp_path: Path) -> None:
    """A linked worktree's directory name must never become the repo identity.

    Run from a real ``git worktree add`` checkout with no remote: identity must
    be the PRIMARY worktree basename, not the linked directory's task name.
    """
    primary = _make_repo(tmp_path, name="real-project", remote=None, branch="main")
    _git(primary, "branch", "feature")
    linked = tmp_path / "wt-task-branch"
    _git(primary, "worktree", "add", str(linked), "feature")

    result = _run(linked, "--default-branch", "main")
    assert result.returncode == 0, result.stderr
    heading = (linked / "AGENTS.md").read_text(encoding="utf-8").splitlines()[0]
    assert heading == "# Agent instructions — real-project"
    assert "wt-task-branch" not in heading


@pytest.mark.parametrize(
    "remote",
    [
        "https://example.com/org/tap_tone_pi.git",  # T02 HTTPS
        "https://example.com/org/tap_tone_pi",       # T02 HTTPS no .git
        "git@example.com:org/tap_tone_pi.git",       # T03 scp-style SSH
        "ssh://git@example.com/org/tap_tone_pi.git",  # T04 ssh:// URL
    ],
)
def test_T02_T04_identity_is_correct_for_every_common_remote_form(
    tmp_path: Path, remote: str
) -> None:
    """Identity right for HTTPS and wrong for SSH would fail where it matters."""
    repo = _make_repo(tmp_path, name=f"wt{abs(hash(remote)) % 9999}", remote=remote)
    assert _run(repo).returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "# Agent instructions — tap_tone_pi"


def test_T05_no_remote_uses_primary_worktree_basename(tmp_path: Path) -> None:
    """With no remote, identity is the primary worktree basename, surfaced.

    In the ordinary (non-linked) case the primary worktree IS the checkout, so
    the basename equals the directory name -- but it is reported so the operator
    can see that no remote named it.
    """
    repo = _make_repo(tmp_path, name="some-checkout", remote=None, branch="main")
    result = _run(repo, "--default-branch", "main")
    assert result.returncode == 0, result.stderr
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "# Agent instructions — some-checkout"
    assert "primary worktree" in result.stdout
    assert "no remote" in result.stdout


def test_T05_repo_name_override_beats_the_fallback(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="some-checkout", remote=None, branch="main")
    assert _run(repo, "--repo-name", "real-name", "--default-branch", "main").returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "# Agent instructions — real-name"


def test_T06_origin_wins_when_another_remote_also_exists(tmp_path: Path) -> None:
    """``origin`` is authoritative even when other remotes are configured."""
    repo = _make_repo(tmp_path, name="wt", remote="https://example.com/o/canonical.git")
    _add_remote(repo, "upstream", "https://example.com/o/other.git")
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    heading = (repo / "AGENTS.md").read_text(encoding="utf-8").splitlines()[0]
    assert heading == "# Agent instructions — canonical"


def test_T07_sole_non_origin_remote_is_authoritative(tmp_path: Path) -> None:
    """Origin absent + exactly one remote: it provides identity AND HEAD (T10)."""
    repo = _make_repo(tmp_path, name="wt", remote=None, branch="main")
    _add_remote(repo, "upstream", "https://example.com/o/canonical.git", head_branch="main")
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "# Agent instructions — canonical"
    assert "## Branch from current `main`. Always." in body


def test_T08_multiple_remotes_without_origin_refuses(tmp_path: Path) -> None:
    """Do not choose the first enumeration result; refuse the ambiguity."""
    repo = _make_repo(tmp_path, name="wt", remote=None, branch="main")
    _add_remote(repo, "alpha", "https://example.com/o/a.git", head_branch="main")
    _add_remote(repo, "beta", "https://example.com/o/b.git", head_branch="main")
    before = _snapshot(repo)

    result = _run(repo, "--default-branch", "main")
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert _snapshot(repo) == before
    assert "multiple remotes" in result.stderr


# --------------------------------------------------------------------------- #
# Default branch (T09-T17)
# --------------------------------------------------------------------------- #


def test_T09_origin_head_resolves(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git", branch="main")
    assert _run(repo).returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Branch from current `main`. Always." in body


def test_T10_sole_non_origin_remote_head_resolves(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="wt", remote=None, branch="trunk")
    _add_remote(repo, "upstream", "https://example.com/o/canonical.git", head_branch="trunk")
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Branch from current `trunk`. Always." in body
    # The branch name was resolved from the sole non-origin remote's HEAD, and
    # identity likewise came from that remote — not from the local 'trunk' or the
    # directory name. (The generated body still references `origin/` by
    # convention; that is the frozen render contract, unchanged here.)
    assert body.splitlines()[0] == "# Agent instructions — canonical"


def test_T11_no_symbolic_remote_head_refuses_even_with_local_main(tmp_path: Path) -> None:
    """origin present but no origin/HEAD, and a local main exists: still refuse.

    The removed behaviour probed local main/master; it must be gone.
    """
    repo = _make_repo(
        tmp_path, name="r", remote="https://example.com/o/r.git", branch="main", origin_head=False
    )
    result = _run(repo)
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert "could not detect the default branch" in result.stderr
    assert "--default-branch" in result.stderr


def test_T12_explicit_existing_local_branch_accepted(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote=None, branch="develop")
    result = _run(repo, "--default-branch", "develop")
    assert result.returncode == 0, result.stderr
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Branch from current `develop`. Always." in body
    assert "git merge-base HEAD origin/develop" in body
    assert "origin/main" not in body


def test_T13_explicit_existing_remote_tracking_branch_accepted(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git", branch="main")
    # A remote-tracking ref that is not a local branch.
    _git(repo, "update-ref", "refs/remotes/origin/release", "HEAD")
    result = _run(repo, "--default-branch", "release")
    assert result.returncode == 0, result.stderr
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Branch from current `release`. Always." in body


def test_T14_explicit_nonexistent_branch_refuses(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git", branch="main")
    before = _snapshot(repo)
    result = _run(repo, "--default-branch", "no-such-branch")
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert _snapshot(repo) == before
    assert "no-such-branch" in result.stderr


def test_T15_current_branch_is_never_a_fallback(tmp_path: Path) -> None:
    """On a checked-out branch with no remote HEAD, do not adopt the current branch."""
    repo = _make_repo(tmp_path, name="r", remote=None, branch="feature-x")
    result = _run(repo)
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert "could not detect the default branch" in result.stderr


def test_T16_lone_local_branch_is_never_a_fallback(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote=None, branch="trunk")
    result = _run(repo)
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()


def test_T17_init_default_branch_config_is_ignored(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote=None, branch="dev")
    _git(repo, "config", "init.defaultBranch", "main")
    result = _run(repo)
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert "could not detect the default branch" in result.stderr


# --------------------------------------------------------------------------- #
# Authority (T18-T24)
# --------------------------------------------------------------------------- #


def test_T18_existing_agents_md_is_preserved(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git")
    assert _run(repo).returncode == 0
    (repo / "AGENTS.md").write_text("HAND EDITED\n", encoding="utf-8")

    result = _run(repo)
    assert result.returncode == 0
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == "HAND EDITED\n"
    assert "SKIP" in result.stdout


def test_T19_claude_md_without_disposition_refuses(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        name="r",
        remote="https://example.com/o/r.git",
        files={"CLAUDE.md": "# Orientation\n"},
    )
    before = _snapshot(repo)
    result = _run(repo)
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()
    assert _snapshot(repo) == before
    assert "CLAUDE.md" in result.stderr
    assert "--authority-disposition" in result.stderr


def test_T20_contributing_md_without_disposition_refuses(tmp_path: Path) -> None:
    """No prose inference: an unrelated CONTRIBUTING.md still refuses closed."""
    repo = _make_repo(
        tmp_path,
        name="r",
        remote="https://example.com/o/r.git",
        files={"CONTRIBUTING.md": "# Contributing\n\nBe kind. File issues.\n"},
    )
    before = _snapshot(repo)
    result = _run(repo)
    assert result.returncode != 0
    assert _snapshot(repo) == before
    assert "not proof that they overlap" in result.stderr


def test_T21_covers_workflow_is_a_successful_no_op(tmp_path: Path) -> None:
    """EXISTING_AUTHORITY_COVERS_WORKFLOW: exit 0, no AGENTS.md, zero mutation."""
    repo = _make_repo(
        tmp_path,
        name="r",
        remote="https://example.com/o/r.git",
        files={"CONTRIBUTING.md": "# Contributing\n\n## Branching\n"},
    )
    before = _snapshot(repo)

    result = _run(repo, "--authority-disposition", "EXISTING_AUTHORITY_COVERS_WORKFLOW")
    assert result.returncode == 0, result.stderr
    assert not (repo / "AGENTS.md").exists()
    assert _snapshot(repo) == before
    assert "SKIP" in result.stdout
    assert "covers" in result.stdout


@pytest.mark.parametrize(
    "disposition", ["COEXISTENCE_EXPLICITLY_ALLOWED", "NO_OVERLAP_CONFIRMED"]
)
def test_T22_T23_approved_coexistence_delegates_rather_than_duplicating(
    tmp_path: Path, disposition: str
) -> None:
    """The CNC pattern: name the local authority, restate nothing it contains."""
    repo = _make_repo(
        tmp_path,
        name="r",
        remote="https://example.com/o/r.git",
        files={"CLAUDE.md": "# Project context\n\nArchitecture rules live here.\n"},
    )

    result = _run(repo, "--authority-disposition", disposition)
    assert result.returncode == 0, result.stderr
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "`CLAUDE.md`" in body
    assert "apply too" in body
    assert "restates nothing found there" in body
    assert "`CLAUDE.md` carry" not in body
    assert "Architecture rules live here" not in body
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").startswith("# Project context")


def test_T24_invalid_disposition_refuses(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git")
    result = _run(repo, "--authority-disposition", "MADE_UP_DISPOSITION")
    assert result.returncode != 0
    assert not (repo / "AGENTS.md").exists()


# --------------------------------------------------------------------------- #
# Consent / atomicity (T25-T28)
# --------------------------------------------------------------------------- #


def test_T25_clean_headless_first_write_needs_no_yes(tmp_path: Path) -> None:
    """A valid clean headless first creation requires no --yes."""
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git")
    result = _run(repo)  # stdin is closed by _run
    assert result.returncode == 0, result.stderr
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "WRITE" in result.stdout
    assert "## Branch from current `main`. Always." in body


def test_T26_existing_agents_force_without_yes_refuses_headlessly(tmp_path: Path) -> None:
    """--force without --yes in a non-TTY refuses before writing anything."""
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git")
    (repo / "AGENTS.md").write_text("ORIGINAL\n", encoding="utf-8")
    before = _snapshot(repo)

    result = _run(repo, "--force")
    assert result.returncode != 0
    assert "EOFError" not in result.stderr and "Traceback" not in result.stderr
    assert _snapshot(repo) == before


def test_T26_force_with_yes_overwrites_headlessly(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git")
    (repo / "AGENTS.md").write_text("ORIGINAL\n", encoding="utf-8")

    result = _run(repo, "--force", "--yes")
    assert result.returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "ORIGINAL" not in body
    assert body.startswith("# Agent instructions — r")


@pytest.mark.parametrize("case", ["invalid_branch", "unresolved_authority", "unresolved_branch"])
def test_T27_force_and_yes_cannot_bypass_truth_checks(tmp_path: Path, case: str) -> None:
    """--force is replacement intent and --yes is consent; neither is authority."""
    if case == "invalid_branch":
        repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git", branch="main")
        args: tuple[str, ...] = ("--default-branch", "no-such-branch", "--force", "--yes")
    elif case == "unresolved_authority":
        repo = _make_repo(
            tmp_path,
            name="r",
            remote="https://example.com/o/r.git",
            files={"CLAUDE.md": "# ctx\n"},
        )
        args = ("--force", "--yes")
    else:  # unresolved_branch
        repo = _make_repo(tmp_path, name="r", remote="https://example.com/o/r.git", origin_head=False)
        args = ("--force", "--yes")

    before = _snapshot(repo)
    result = _run(repo, *args)
    assert result.returncode != 0, case
    assert not (repo / "AGENTS.md").exists(), case
    assert _snapshot(repo) == before, case


def test_T28_atomic_write_failure_preserves_original(tmp_path: Path, monkeypatch) -> None:
    """A failed os.replace leaves the original intact and removes the temp file.

    Mocking is authorised here and only here: a replacement failure cannot be
    provoked from outside the process.
    """
    module = _load_module()
    target = tmp_path / "AGENTS.md"
    target.write_text("ORIGINAL\n", encoding="utf-8")

    def boom(src, dst):  # noqa: ANN001
        raise OSError("simulated replace failure")

    monkeypatch.setattr(module.os, "replace", boom)

    with pytest.raises(OSError):
        module.atomic_write_text(target, "NEW CONTENT THAT MUST NOT LAND")

    assert target.read_text(encoding="utf-8") == "ORIGINAL\n"
    residue = [p.name for p in tmp_path.iterdir() if p.name != "AGENTS.md"]
    assert residue == [], f"temporary residue left behind: {residue}"


# --------------------------------------------------------------------------- #
# The generated artifact stays narrow (frozen semantics, Section 8)
# --------------------------------------------------------------------------- #


def test_non_git_directory_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    result = _run(plain)
    assert result.returncode == 2
    assert not (plain / "AGENTS.md").exists()


def test_no_incident_history_is_invented_or_stubbed(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, name="r7", remote="https://example.com/o/r7.git")
    assert _run(repo).returncode == 0
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "INCIDENTS" not in body
    assert "VERIFICATION GATES" not in body
    assert "TODO" not in body
    for foreign in ("#12", "#13", "#14", "CNC Production Shop"):
        assert foreign not in body


def test_the_tool_has_no_pr_template_capability_at_all(tmp_path: Path) -> None:
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
    for foreign in ("## Repository Structure", "## Architecture", "## Testing"):
        assert foreign not in body


# --------------------------------------------------------------------------- #
# Every refusal path leaves the repository untouched (frozen invariant)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "case",
    [
        "undetectable_branch",
        "force_without_yes",
        "authority_unresolved",
        "multiple_remotes",
    ],
)
def test_every_refusal_path_mutates_nothing(tmp_path: Path, case: str) -> None:
    """Generalizes the EOFError-after-first-write defect beyond its one shape."""
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
    else:  # multiple_remotes
        repo = _make_repo(tmp_path, name="z4", remote=None, branch="main")
        _add_remote(repo, "a", "https://example.com/o/a.git", head_branch="main")
        _add_remote(repo, "b", "https://example.com/o/b.git", head_branch="main")
        args = ("--default-branch", "main")

    before = _snapshot(repo)
    result = _run(repo, *args)

    assert result.returncode != 0, case
    assert _snapshot(repo) == before, case
    assert "Traceback" not in result.stderr, case
    assert result.stderr.startswith("refused:"), case
