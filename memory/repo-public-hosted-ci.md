---
name: repo-public-hosted-ci
description: "Repo is PUBLIC (since 2026-06-12) on hosted runners by operator decision — self-hosted CI was built and fully reversed; don't re-propose it"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

project-wolf went PUBLIC on 2026-06-12 to resolve the GitHub Actions billing outage (private-repo hosted minutes exhausted). The operator explicitly chose public+hosted over private+self-hosted; a complete self-hosted migration (runner `wolf-dev-runner`, containerized job layout, PR #17) was built and then fully reversed on request — don't re-propose self-hosted runners.

Hardening in place (API-verified 2026-06-13): rulesets `protect-main` (no force-push/deletion, no bypass for anyone) and `protect-release-tags` (v* tags admin-only); workflow token read-only; fork-PR workflows need approval for all external contributors; secret scanning + push protection; Dependabot alerts + security updates; private vulnerability reporting; org-wide 2FA.

**Why:** force-pushing main is now impossible without first disabling the ruleset; fork PRs never run CI without operator approval; v* tags (which trigger GPG-signed releases) are admin-only.

**How to apply:** treat public visibility as a constraint — gitleaks gate is load-bearing, never commit lab secrets, Actions logs are world-readable. Dismissed-with-reason alerts: ecdsa Minerva (HS256-only JWTs, no patch; long-term idea: python-jose → PyJWT) and torch jit.script (no untrusted TorchScript path, no patch — revisit when one ships). Related: [[dependabot-uv-lock-only-prs]].
