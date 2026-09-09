#!/usr/bin/env python3
"""Establish the portfolio's branch/PR workflow entry point in a repository.

Writes a narrow ``AGENTS.md`` carrying **branch and pull-request workflow
only**. It does not write project or architecture instructions, and it has no
PR-template capability of any kind — a future change adding one would then be a
visible scope expansion rather than the activation of dormant machinery.

Every defect in this tool's history was ENVIRONMENT-COUPLING: a worktree
directory name mistaken for a repository name, a case-insensitive filesystem, a
non-TTY stdin, an undetectable default branch. None was visible by reading the
code. So the shape of the program is:

    DISCOVER  ->  VALIDATE  ->  DECIDE ENTIRE WRITE SET  ->  CONSENT  ->  WRITE

and never

    WRITE FILE A  ->  discover a problem  ->  abort half-complete

A refusal leaves the target byte-identical. That is the whole point: this tool
modifies repositories it does not own.

**It does not interpret prose.** It detects the *presence* of authority
documents — ``AGENTS.md``, ``CLAUDE.md``, ``CONTRIBUTING.md`` — and treats
presence as a signal to stop and ask, never as proof of overlap. Reading
``CONTRIBUTING.md`` and concluding from a heading that it "covers workflow"
would make this an unreliable governance classifier instead of a distribution
utility, so an unresolved signal fails closed and the operator disposes of it
explicitly.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

# Wrap width for generated prose, matching ordinary Markdown convention.
WRAP = 74

# Documents whose presence means an authority may already own this ground. Their
# presence is a signal to stop and inspect, NOT proof that they overlap.
# ``AGENTS.md`` is handled separately: it is the artifact this tool writes, so an
# existing one is an overwrite question rather than a collision question.
AUTHORITY_SIGNALS = ("CLAUDE.md", "CONTRIBUTING.md")

TARGET = "AGENTS.md"

# How an operator may dispose of an authority signal. The tool cannot reach any
# of these conclusions itself.
DISPOSITIONS = {
    "EXISTING_AUTHORITY_COVERS_WORKFLOW": False,
    "COEXISTENCE_EXPLICITLY_ALLOWED": True,
    "NO_OVERLAP_CONFIRMED": True,
}

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NOT_A_REPO = 2

# Where a repository name comes from, in preference order. Recorded rather than
# implied, because "the directory is named for the repository" is exactly the
# assumption that produced a wrong file from a worktree.
IDENTITY_OPERATOR = "operator (--repo-name)"
IDENTITY_REMOTE = "origin remote"
IDENTITY_DIRECTORY = "directory name (fallback: no origin remote)"


class Refusal(Exception):
    """A preflight condition that forbids writing. Carries an exit code."""

    def __init__(self, message: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.code = code


def git(repo: Path, *args: str) -> str | None:
    """Run git in ``repo``; return stripped stdout, or None if it failed."""
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def repo_name_from_remote(url: str) -> str | None:
    """The repository name in an origin URL, whatever form the URL takes.

    Handles HTTPS, ssh://, and scp-style ``git@host:org/name.git``. A tool whose
    identity is right for HTTPS and wrong for the SSH form developers actually
    use would be wrong in exactly the cases that matter.
    """
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return None
    # scp-style and URL forms both end in .../name[.git]; split on either
    # separator so ``git@host:org/name.git`` yields ``name``.
    tail = re.split(r"[/:]", cleaned)[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    return tail or None


@dataclass(frozen=True)
class RepositoryFacts:
    """What discovery established. Facts or explicit absence — never a guess."""

    root: Path
    name: str
    name_source: str
    default_branch: str | None
    default_branch_source: str
    existing_target: bool
    authority_signals: tuple[str, ...]


def discover(repo: Path, repo_name: str | None, default_branch: str | None) -> RepositoryFacts:
    """Gather every fact needed to decide. Writes nothing."""
    if not repo.is_dir():
        raise Refusal(f"{repo} is not a directory", EXIT_NOT_A_REPO)

    inside = git(repo, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        raise Refusal(f"{repo} is not a git repository", EXIT_NOT_A_REPO)

    top = git(repo, "rev-parse", "--show-toplevel")
    root = Path(top) if top else repo

    if repo_name:
        name, name_source = repo_name, IDENTITY_OPERATOR
    else:
        remote = git(repo, "remote", "get-url", "origin")
        derived = repo_name_from_remote(remote) if remote else None
        if derived:
            name, name_source = derived, IDENTITY_REMOTE
        else:
            # A worktree directory is named for the task, not the repository, so
            # this fallback is reported rather than applied silently.
            name, name_source = root.name, IDENTITY_DIRECTORY

    if default_branch:
        branch, branch_source = default_branch, "operator (--default-branch)"
    else:
        branch, branch_source = detect_default_branch(repo)

    signals = tuple(s for s in AUTHORITY_SIGNALS if (root / s).is_file())

    return RepositoryFacts(
        root=root,
        name=name,
        name_source=name_source,
        default_branch=branch,
        default_branch_source=branch_source,
        existing_target=(root / TARGET).is_file(),
        authority_signals=signals,
    )


def detect_default_branch(repo: Path) -> tuple[str | None, str]:
    """The repository's default branch, or ``None`` if it cannot be established.

    Never falls back to "main". Writing "branch from main" into a canonical file
    naming a branch that is not this repository's default would replicate that
    error across every repository scaffolded the same way.
    """
    head = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
    if head and head.startswith("refs/remotes/origin/"):
        return head[len("refs/remotes/origin/") :], "origin/HEAD"

    for candidate in ("main", "master"):
        if git(repo, "rev-parse", "--verify", f"refs/heads/{candidate}"):
            return candidate, f"local branch {candidate}"

    return None, "undetectable"


@dataclass(frozen=True)
class WritePlan:
    """The complete decision, reached before anything is written."""

    action: str  # WRITE | SKIP
    reason: str
    notes: tuple[str, ...]


def plan(facts: RepositoryFacts, *, force: bool, yes: bool, disposition: str | None) -> WritePlan:
    """Decide the entire write set, or refuse. Writes nothing.

    Order matters. An existing target is an overwrite question and is settled
    first — on a second run there is nothing left to decide about authority,
    because the artifact is already there.
    """
    notes: list[str] = []

    if facts.default_branch is None:
        raise Refusal(
            "could not detect the default branch: no origin/HEAD, and no local "
            "main or master. Re-run with --default-branch <name>. Refusing "
            "rather than writing a file that names a branch this repository "
            "may not have."
        )

    if facts.existing_target:
        if not force:
            return WritePlan("SKIP", f"{TARGET} already exists", tuple(notes))
        if not yes:
            raise Refusal(
                f"--force would overwrite the existing {TARGET}, and this is a "
                "non-interactive run. Pass --yes to consent explicitly. Nothing "
                "has been written."
            )
        notes.append(f"overwriting existing {TARGET} under --force --yes")

    if facts.authority_signals:
        present = ", ".join(facts.authority_signals)
        if disposition is None:
            raise Refusal(
                f"{present} present in {facts.root.name}. Their presence is a "
                "signal to stop and inspect, not proof that they overlap — and "
                "this tool does not read them to decide. Resolve it explicitly "
                "with --authority-disposition "
                f"{{{'|'.join(DISPOSITIONS)}}}. Nothing has been written."
            )
        if not DISPOSITIONS[disposition]:
            raise Refusal(
                f"operator disposition {disposition}: an existing authority "
                f"({present}) already owns branch and pull-request workflow "
                f"here, so no {TARGET} is warranted. Nothing has been written."
            )
        notes.append(f"authority signals {present} disposed as {disposition}")

    if facts.name_source == IDENTITY_DIRECTORY:
        notes.append(
            f"repository name '{facts.name}' came from the directory: there is "
            "no origin remote. A worktree is named for its task, so confirm "
            "this or pass --repo-name."
        )

    return WritePlan("WRITE", f"no {TARGET}, no unresolved authority", tuple(notes))


def render(facts: RepositoryFacts) -> str:
    """The narrow workflow entry point. Branch and PR discipline, nothing else.

    Deliberately absent: any incident history. An incident that justified a rule
    in one repository is evidence for that rule, not text to copy into a
    repository where it never happened — and a placeholder implying every
    repository owes one is the same error with a TODO on it.
    """
    branch = facts.default_branch
    lines = [
        f"# Agent instructions — {facts.name}",
        "",
        "Read this before creating a branch or opening a pull request. It is",
        "read by Cursor, Codex, and other coding agents.",
        "",
    ]

    if facts.authority_signals:
        # Phrased to read correctly for one authority or several, rather than
        # branching on the count, and wrapped rather than hand-split so the
        # line length does not depend on how many names land in it. A generated
        # artifact goes to repositories nobody will proofread it in.
        named = " and ".join(f"`{s}`" for s in facts.authority_signals)
        lines += [
            textwrap.fill(
                "Project, architecture and contribution rules for this "
                f"repository live in {named} and apply too. This file adds "
                "branch and pull-request workflow only, and restates nothing "
                "found there.",
                width=WRAP,
            ),
            "",
        ]

    lines += [
        f"## Branch from current `{branch}`. Always.",
        "",
        "```bash",
        "git fetch origin",
        f"git switch -c <branch-name> origin/{branch}",
        "```",
        "",
        "Not from the workspace's current `HEAD`. Not from another branch. Not",
        "from a branch belonging to a pull request that has not merged yet.",
        "",
        "A stale base is not a style problem. It produces pull requests that",
        "carry commits nobody intended to submit, and each one has to be",
        "reconciled by hand.",
        "",
        "## Self-check before opening a pull request",
        "",
        "```bash",
        f"git merge-base HEAD origin/{branch}",
        f"git rev-parse origin/{branch}",
        "```",
        "",
        f"These must match. If they do not, the branch is not based on current",
        f"`origin/{branch}` and should be rebased before review.",
        "",
        "## Scope",
        "",
        "One pull request does one thing. If a tangent emerges while working,",
        "record it rather than folding it in — a reviewer cannot separate two",
        "intentions that arrived in one diff.",
        "",
    ]
    return "\n".join(lines)


def report(facts: RepositoryFacts, decision: WritePlan, written: Path | None) -> None:
    print(f"repository       {facts.name}  ({facts.name_source})")
    print(f"default branch   {facts.default_branch}  ({facts.default_branch_source})")
    signals = ", ".join(facts.authority_signals) if facts.authority_signals else "none"
    print(f"authority        {signals}")
    print(f"{decision.action:<16} {decision.reason}")
    for note in decision.notes:
        print(f"  note           {note}")
    if written is not None:
        print(f"  wrote          {written}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Establish the portfolio's branch/PR workflow entry point in a "
            "repository. Refuses rather than guessing, and writes nothing "
            "until every condition is resolved."
        )
    )
    parser.add_argument("repo", help="path to the target repository")
    parser.add_argument(
        "--default-branch",
        default=None,
        help="the repository's default branch, where it cannot be detected",
    )
    parser.add_argument(
        "--repo-name",
        default=None,
        help="override repository identity (normally taken from origin)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"permit overwriting an existing {TARGET}; requires --yes",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="consent non-interactively to a destructive action",
    )
    parser.add_argument(
        "--authority-disposition",
        choices=sorted(DISPOSITIONS),
        default=None,
        help="how an existing authority document has been dispositioned",
    )
    args = parser.parse_args(argv)

    try:
        facts = discover(Path(args.repo), args.repo_name, args.default_branch)
        decision = plan(
            facts,
            force=args.force,
            yes=args.yes,
            disposition=args.authority_disposition,
        )
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return refusal.code

    # Everything is resolved. Only now does anything change on disk.
    written: Path | None = None
    if decision.action == "WRITE":
        target = facts.root / TARGET
        target.write_text(render(facts), encoding="utf-8")
        written = target

    report(facts, decision, written)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
