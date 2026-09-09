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
--authority-disposition EXISTING_AUTHORITY_COVERS_WORKFLOW   -> refuse, recorded
--authority-disposition COEXISTENCE_EXPLICITLY_ALLOWED       -> write, delegating
--authority-disposition NO_OVERLAP_CONFIRMED                 -> write
```

Where coexistence is allowed, the generated file **names** the existing
authorities and defers to them rather than restating anything they contain.

### Repository identity

Taken from the `origin` remote, not the directory name — a worktree is named for
the task, not the repository. HTTPS, SSH and `scp`-style remotes are all
understood. With no remote, the directory name is used as a **documented
fallback** and the report says so; `--repo-name` overrides it.

## Tests

```bash
pytest -q
```

Real temporary Git repositories, real CLI subprocesses, stdin closed. No mocks —
the defects this tool is hardened against were all environmental, and a mocked
environment would only test the model of it.
