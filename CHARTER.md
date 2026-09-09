# portfolio-repo-tools — Charter

> **Governing responsibility**
>
> **Tools that create, distribute, validate, or migrate repository-level
> development infrastructure across the portfolio, where that capability is not
> properly owned by an individual product repository.**

---

## Why this repository exists

A survey of the 59 repositories in this portfolio found a small but real class of
**repository-distribution tooling with no owner**:

- `scripts/Create-TestDummy.ps1` exists in `luthiers-toolbox` and has been
  **copy-propagated** into `ltb-express/scripts/` and the consolidation lab's
  `reference/` tree. Three copies, no owner, already drifting.
- A scaffolder for agent workflow instructions had a written self-test and a
  documented defect history, but **no committed implementation anywhere in the
  org**.
- `AGENTS.md` exists in exactly two repositories, and the two files serve
  **different purposes** under the same filename — so the convention the
  scaffolder would propagate was never actually settled.

Every candidate home was examined and excluded:

| Candidate | Why not |
| --- | --- |
| `luthiers-toolbox` | A product repo. Its `scripts/ci/` accumulated cross-cutting checkers by proximity, not by charter |
| `luthiers-toolbox-consolidation-lab` | Its own charter forbids it: *"may observe, model, and propose. It may not directly become production code"* |
| `everything-claude-code` | Claude Code plugin configuration. `AGENTS.md` is a Codex/Cursor convention; wrong ecosystem, and its `commands/` are slash commands, not bootstrap tooling |
| `awesomeAgentskills` | Curated content, not tooling |
| `code-rescue-tool` | Consumes `code-analysis-tool` findings. Different domain |
| `ltb-golden-master` | A *target* fixture for repo-creation workflows, not an owner |
| `tap_tone_pi` | A measurement instrument toolchain whose own boundary routes non-measurement capability elsewhere |

So this repository was created because ownership genuinely did not exist — not
because none of the first searches happened to fit.

## The ownership test

```text
Does the tool operate on repository infrastructure
across multiple product repositories?
                    │
             ┌──────┴──────┐
            YES            NO
             │              │
             ▼              ▼
 portfolio tooling      the product repo
 candidate              owns it
```

A tool belongs here only when the answer is **yes**. A tool that operates on one
repository's own domain belongs to that repository, however convenient it would
be to keep all the utilities together.

## What this repository is not

**It is not a governance authority.** It distributes and validates
infrastructure; it does not decide what any repository's rules should be. Where a
target repository already has an authority covering the same ground, the correct
behaviour is to refuse and report — never to overwrite, and never to fork.

**It is not an agent-tooling repository.** The first capability concerns agent
workflow instructions, but the responsibility above is deliberately broader than
any one agent ecosystem and is not named for one.

**It is not a consolidation project.** Creating this owner does not authorize
extracting existing tooling from the repositories that currently hold it.
`luthiers-toolbox/scripts/ci/`, `core_ci.yml` and the three copies of
`Create-TestDummy.ps1` all stay where they are. Migration is evidence-driven and
happens per-tool, later, if at all.

## Standing rules

**Fail closed on ambiguity.** A tool here modifies repositories it does not own.
Where it cannot establish a fact — repository identity, default branch, whether
an existing authority overlaps — it refuses and says why. It does not guess, and
it does not write a file it cannot name correctly.

**Preflight before mutation.** Every environmental fact and every required
permission is resolved before the first byte is written. A refusal must leave the
target byte-identical. A half-completed write is worse than a refusal, because
the operator now has to work out what happened.

**No semantic interpretation of prose.** These tools detect the *presence* of
authority documents. They do not read them and conclude what they mean. A tool
that decided from a heading whether `CONTRIBUTING.md` "covers workflow" would be
an unreliable governance classifier wearing a utility's clothes.

**Do not propagate one repository's history into another.** An incident that
justified a rule in one repository is evidence for that rule, not text to copy
into repositories where it never happened.

**Test against real repositories.** The defect history that motivated the first
capability was entirely environmental — a worktree directory name, a
case-insensitive filesystem, a non-TTY stdin, an undetectable default branch.
None was visible by reading the code. Tests here build real temporary Git
repositories and drive the real CLI as a subprocess, asserting on output rather
than on internal calls. Mocking the environment tests the model of the
environment, and a wrong model of it is the whole defect history.

## Deployment

Nothing here is deployed across the portfolio because its tests pass. A new
capability is piloted against **one deliberately selected repository**, its
output is inspected by hand, and only then is portability judged.
