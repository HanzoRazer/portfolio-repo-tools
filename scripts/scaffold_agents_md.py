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
modifies repositories it does not own. And the write itself is atomic — rendered
to a sibling temp file and moved into place with ``os.replace`` — so an
interrupted or failing write can never expose a half-written ``AGENTS.md``.

**It does not interpret prose.** It detects the *presence* of authority
documents — ``AGENTS.md``, ``CLAUDE.md``, ``CONTRIBUTING.md`` — and treats
presence as a signal to stop and ask, never as proof of overlap. Reading
``CONTRIBUTING.md`` and concluding from a heading that it "covers workflow"
would make this an unreliable governance classifier instead of a distribution
utility, so an unresolved signal fails closed and the operator disposes of it
explicitly.

Repository facts are established without guessing:

* the **authoritative remote** is ``origin`` when present, otherwise a sole
  non-origin remote; several remotes without an ``origin`` are an ambiguity the
  tool refuses rather than resolves by enumeration order;
* **identity** comes from that remote's URL, or — with no remote — from the
  primary worktree's basename, never a linked worktree's task directory name;
* the **default branch** comes from an explicit, existing ``--default-branch``
  or the authoritative remote's symbolic HEAD, and nothing else. It never guesses
  a local ``main``/``master``, which would replicate a wrong branch name across
  every repository scaffolded the same way.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
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
# of these conclusions itself. COVERS is a *successful no-op*: the existing
# authority already owns the workflow, so no new artifact is warranted and the
# tool exits 0 having written nothing. The other two authorize a write.
DISPOSITION_COVERS = "EXISTING_AUTHORITY_COVERS_WORKFLOW"
DISPOSITION_WRITES = ("COEXISTENCE_EXPLICITLY_ALLOWED", "NO_OVERLAP_CONFIRMED")
DISPOSITIONS = (DISPOSITION_COVERS, *DISPOSITION_WRITES)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NOT_A_REPO = 2

# Where a repository name comes from, in preference order. Recorded rather than
# implied, because "the directory is named for the repository" is exactly the
# assumption that produced a wrong file from a worktree.
IDENTITY_OPERATOR = "operator (--repo-name)"
IDENTITY_PRIMARY_WORKTREE = "primary worktree (no remote)"


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
    """The repository name in a remote URL, whatever form the URL takes.

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


def select_authoritative_remote(repo: Path) -> str | None:
    """The remote whose identity and HEAD this tool trusts, or None.

    ``origin`` when it exists. Otherwise a sole non-origin remote — a repository
    with one ``upstream`` and no ``origin`` still has an unambiguous authority.
    Several remotes without an ``origin`` are an ambiguity, and choosing the
    first enumeration result is exactly the kind of guess this tool refuses.
    """
    listed = git(repo, "remote")
    remotes = [r for r in (listed or "").splitlines() if r.strip()]
    if "origin" in remotes:
        return "origin"
    if not remotes:
        return None
    if len(remotes) == 1:
        return remotes[0]
    raise Refusal(
        "no origin remote, and multiple remotes are present "
        f"({', '.join(sorted(remotes))}). Refusing to choose one by enumeration "
        "order — configure an origin, or reduce to a single remote. Nothing has "
        "been written."
    )


def primary_worktree_name(repo: Path) -> str | None:
    """Basename of the primary (main) worktree, from Git's own metadata.

    A linked worktree added with ``git worktree add`` is named for the task it
    serves, not for the repository, so its directory name must never become
    identity. ``git worktree list --porcelain`` lists the primary worktree
    first; that is the one whose basename names the repository.
    """
    out = git(repo, "worktree", "list", "--porcelain")
    if not out:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :].strip()
            return Path(path).name if path else None
    return None


def branch_ref_exists(repo: Path, branch: str) -> bool:
    """Whether ``branch`` names a real branch — local or remote-tracking.

    Accepts ``refs/heads/<branch>`` or any ``refs/remotes/<remote>/<branch>``.
    Rejects the symbolic ``refs/remotes/<remote>/HEAD`` pointer and anything that
    does not exist: an explicit default branch must be a branch, not a guess and
    not a remote's HEAD alias.
    """
    if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is not None:
        return True
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes/") or ""
    prefix = "refs/remotes/"
    for line in refs.splitlines():
        if not line.startswith(prefix):
            continue
        remainder = line[len(prefix) :]  # <remote>/<tracked>
        if "/" not in remainder:
            continue
        _, tracked = remainder.split("/", 1)
        if tracked == "HEAD":
            continue
        if tracked == branch:
            return True
    return False


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

    remote = select_authoritative_remote(repo)

    if repo_name:
        name, name_source = repo_name, IDENTITY_OPERATOR
    elif remote is not None:
        url = git(repo, "remote", "get-url", remote)
        derived = repo_name_from_remote(url) if url else None
        if not derived:
            raise Refusal(
                f"the '{remote}' remote is set but its URL did not yield a "
                "repository name. Pass --repo-name. Nothing has been written."
            )
        name, name_source = derived, f"{remote} remote"
    else:
        # No remote: identity is the primary worktree's basename, from Git
        # metadata — never the current (possibly linked) worktree's directory.
        primary = primary_worktree_name(repo)
        if not primary:
            raise Refusal(
                "no remote is configured and the primary worktree could not be "
                "established from Git metadata, so repository identity is "
                "unknown. Pass --repo-name. Nothing has been written."
            )
        name, name_source = primary, IDENTITY_PRIMARY_WORKTREE

    if default_branch:
        # An explicit default branch must actually exist. --yes and --force do
        # not bypass this: they are consent and replacement intent, not a licence
        # to name a branch the repository does not have.
        if not branch_ref_exists(repo, default_branch):
            raise Refusal(
                f"--default-branch {default_branch}: no such branch. Expected "
                f"refs/heads/{default_branch} or a remote-tracking "
                f"refs/remotes/*/{default_branch}. Nothing has been written."
            )
        branch, branch_source = default_branch, "operator (--default-branch)"
    else:
        branch, branch_source = detect_default_branch(repo, remote)

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


def detect_default_branch(repo: Path, remote: str | None) -> tuple[str | None, str]:
    """The default branch from the authoritative remote's HEAD, or ``None``.

    Never falls back to a local ``main``/``master``. Writing "branch from main"
    into a canonical file naming a branch that is not this repository's default
    would replicate that error across every repository scaffolded the same way.
    The only sources are an explicit override (handled by the caller) and the
    authoritative remote's symbolic HEAD.
    """
    if remote is None:
        return None, "undetectable"
    prefix = f"refs/remotes/{remote}/"
    head = git(repo, "symbolic-ref", f"{prefix}HEAD")
    if head and head.startswith(prefix):
        return head[len(prefix) :], f"{remote}/HEAD"
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
            "could not detect the default branch: the authoritative remote has "
            "no symbolic HEAD (or there is no remote), and this tool does not "
            "guess from local branches. Re-run with --default-branch <name>. "
            "Refusing rather than writing a file that names a branch this "
            "repository may not have."
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
        if disposition == DISPOSITION_COVERS:
            # A successful no-op: the existing authority already owns branch and
            # pull-request workflow here, so no AGENTS.md is warranted. This is
            # not a refusal — the operator asked the right question and the
            # answer is "nothing to do".
            return WritePlan(
                "SKIP",
                f"existing authority ({present}) covers branch and pull-request "
                "workflow; no new artifact warranted",
                tuple(notes),
            )
        notes.append(f"authority signals {present} disposed as {disposition}")

    if facts.name_source == IDENTITY_PRIMARY_WORKTREE:
        notes.append(
            f"repository name '{facts.name}' came from the primary worktree: "
            "there is no remote to name it. A worktree is named for its task, so "
            "confirm this or pass --repo-name."
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


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Render to a sibling temp file on the same filesystem, flush and (where
    practical) fsync it, then ``os.replace`` it into place — a single atomic
    rename. A failure at any point leaves the original ``path`` untouched and
    removes the temporary file rather than exposing a partial write.
    """
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(directory))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                # fsync is not available or meaningful on every filesystem; the
                # rename below is what provides atomicity.
                pass
        os.replace(tmp_path, path)
    except BaseException:
        # os.replace consumes the temp on success; on any failure before or
        # during it, remove the residue and preserve the original.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


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
        help="override repository identity (normally taken from the remote)",
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

    # Everything is resolved. Only now does anything change on disk, and even
    # then through an atomic replace.
    written: Path | None = None
    if decision.action == "WRITE":
        target = facts.root / TARGET
        atomic_write_text(target, render(facts))
        written = target

    report(facts, decision, written)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
