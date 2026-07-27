# Module 01: Repository Foundation

## Problem

The original Git repository lived inside `backend/`. Files in `docs/`, `infra/`,
`frontend/`, and `scripts/` were therefore outside version control even though they
belonged to the same product.

## Design

The Git worktree now starts at the project root. The existing commit history is
preserved, while the old tracked backend paths receive a `backend/` prefix. A single
root `.gitignore` defines repository-wide policy.

```text
enterprise-ai-assistant/       <- Git worktree
├── backend/                   <- Python application
├── frontend/                  <- Vue application
├── infra/                     <- local and deployment infrastructure
├── docs/                      <- architecture and learning material
└── scripts/                   <- developer utilities
```

## Important ideas

- A Git worktree boundary determines which project files Git can track.
- The index is Git's proposed next snapshot; it is not the same thing as the working
  directory.
- Prefixing the index with `backend/` records the repository relocation without
  rewriting existing source files or discarding local modifications.
- Secrets, virtual environments, caches, generated logs, and build outputs are
  ignored. Tests and documentation are source code and must be tracked.

## Verification

- The current branch is `codex/week1-foundation`.
- Existing backend history remains reachable.
- Local memory-pipeline edits remain unstaged and unchanged.
- Root documentation and infrastructure files are visible to Git.
- Runtime log files are no longer tracked.

## Interview explanation

"I converted an accidentally nested backend-only repository into a monorepo-style
worktree. I preserved history, separated the index migration from existing business
changes, and centralized ignore rules so tests and deployment assets are reviewed
with the application."

## Exercise

Explain the difference between the working directory, staging area, and commit. Then
identify where a newly created `backend/tests/test_health.py` would appear in each
stage of the Git lifecycle.

