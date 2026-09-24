# portfolio-repo-tools

Tools that create, distribute, validate, or migrate **repository-level
development infrastructure** across the portfolio, where that capability is not
properly owned by an individual product repository.

See [CHARTER.md](CHARTER.md) for the ownership test and the standing rules. The
short version: these tools modify repositories they do not own, so they fail
closed, resolve everything before writing anything, and never interpret prose.

## Capabilities

| Tool | Status | What it does |
| --- | --- | --- |
| [`scripts/scaffold_agents_md.py`](scripts/scaffold_agents_md.py) | v1, **not deployed** | Establishes the portfolio's branch/PR workflow entry point in a repository that does not already have overlapping authority |

## `scaffold_agents_md`

Writes a narrow `AGENTS.md` carrying **branch and pull-request workflow only**.
It does not write project or architecture instructions, and it has no
PR-template capability of any kind.

```bash
python scripts/scaffold_agents_md.py <repo>
python scripts/scaffold_agents_md.py <repo> --default-branch develop
python scripts/scaffold_agents_md.py <repo> --authority-disposition NO_OVERLAP_CONFIRMED
python scripts/scaffold_agents_md.py <repo> --force --yes
```

### It refuses more often than it writes, on purpose

| Condition | Result |
| --- | --- |
| Not a Git repository | exit 2 |
| Default branch cannot be established | refuse, suggest `--default-branch` |
| `AGENTS.md` already exists | skip, byte-identical |
| `--force` without `--yes` in a non-TTY | refuse before writing anything |
| `CLAUDE.md` or `CONTRIBUTING.md` present | refuse — an operator must dispose of the overlap |

That last one is the important one. Their **presence is a signal to stop and
inspect, not proof that they overlap.** v1 is not authorized to read them and
decide, so it hands the question back:

```
--authority-disposition EXISTING_AUTHORITY_COVERS_WORKFLOW   -> successful no-op, recorded
--authority-disposition COEXISTENCE_EXPLICITLY_ALLOWED       -> write, delegating
--authority-disposition NO_OVERLAP_CONFIRMED                 -> write
```

`EXISTING_AUTHORITY_COVERS_WORKFLOW` is a **successful no-op**: the existing
authority already owns branch and pull-request workflow here, so no `AGENTS.md`
is warranted, nothing is written, and the tool exits `0`. It is not a refusal —
the operator asked the right question and the answer is "nothing to do".

Where coexistence is allowed, the generated file **names** the existing
authorities and defers to them rather than restating anything they contain.

### Repository identity

The authoritative remote is `origin` when present; otherwise a **sole**
non-origin remote (an `upstream` with no `origin` is still unambiguous). Several
remotes without an `origin` are an ambiguity the tool **refuses** rather than
resolving by enumeration order.

Identity comes from that remote's URL, not the directory name — a worktree is
named for the task, not the repository. HTTPS, SSH and `scp`-style remotes are
all understood. With **no remote at all**, identity is the **primary worktree's
basename** resolved from Git metadata — never a linked worktree's task directory
name — and the report says so; `--repo-name` overrides it.

### Default branch

Resolved in exactly this order, with no other source:

```
1. explicit --default-branch  (accepted only if that branch actually exists)
2. the authoritative remote's symbolic HEAD
3. refuse
```

There is no fallback to a local `main`/`master`: writing "branch from main" into
a canonical file naming a branch that is not this repository's default would
replicate that error across every repository scaffolded the same way.

### Consent and force

> `--force` is replacement intent, not permission to bypass repository-truth checks.

> `--yes` is consent, not authority.

Neither bypasses an unresolved identity, an unresolved or nonexistent default
branch, an authority collision, or an invalid disposition. A valid clean
headless first creation needs no `--yes`; overwriting an existing `AGENTS.md`
under `--force` in a non-TTY requires it.

The write itself is atomic: the file is rendered to a sibling temp file and moved
into place, so a failed or interrupted write leaves the original `AGENTS.md`
untouched rather than exposing a half-written file.

## Tests

```bash
pytest -q
```

Real temporary Git repositories, real CLI subprocesses, stdin closed. No mocks —
the defects this tool is hardened against were all environmental, and a mocked
environment would only test the model of it.
