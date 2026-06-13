---
name: memory-mirrored-into-repo
description: STANDING RULE (2026-06-13) — every memory lives in BOTH the canonical ~/.claude memory dir AND the in-repo memory/ dir, kept byte-identical; the repo copy is git-tracked, never gitignored
metadata:
  type: feedback
---

STANDING RULE (2026-06-13): the operator directed that all memory work be maintained in two places, kept fully in sync:

1. **Canonical (live system):** `~/.claude/projects/-home-alsechemist-Codespace-project-wolf/memory/` — the directory the memory system reads/writes.
2. **In-repo mirror:** `/home/alsechemist/Codespace/project-wolf/memory/` — git-tracked, committed alongside the code. **Never add this path to `.gitignore`.**

**Why:** the operator wants the project's accumulated memory versioned in the repository itself — surviving alongside the code history, reviewable in PRs, and not living only in a machine-local `~/.claude` dir that a fresh clone wouldn't have.

**How to apply:** after ANY create / update / delete of a memory file or of `MEMORY.md` — on either side — replicate the exact change to the other directory so the two stay byte-identical. The simplest mechanical guarantee is `rsync -a --delete <source>/ <dest>/` after a batch of memory edits, then `diff -r` to confirm zero drift. When the live memory system writes to the canonical dir mid-session, mirror that write into the repo dir before ending the turn. Reconciled 2026-06-13: the two had diverged (repo held the newer/curated content incl. [[repo-public-hosted-ci]] context); resolved by a lossless newest-wins merge. Related: [[dependabot-uv-lock-only-prs]].
