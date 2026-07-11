---
name: postgres-18-baseline
description: "PostgreSQL 18 fully replaced 17 (2026-07-11, operator decision): wolf-database gate REJECTS 17, packaging/CI/docs all 18; live dev cluster upgrade is a privileged operator runbook (pg_upgradecluster)"
metadata:
  type: project
---

Wolf's Postgres baseline is **PostgreSQL 18 + pgvector** since 2026-07-11
(operator decision; ADR 0008 addendum) — a FULL replacement, not a
dual-support window. `wolf_database.binaries.REQUIRED_MAJOR_VERSION = 18`;
the version gate rejects a 17 install (pinned by
`test_verify_postgres_supported_rejects_postgres_17`); debian/control
Depends `postgresql-18` + `postgresql-18-pgvector` (pgdg noble 0.8.4); CI
uses `pgvector/pgvector:pg18` service images + pgdg-18 smoke installs.

**Why:** operator wants Wolf natively on 18 ("fully and natively supporting
Wolf as its components, replacing 17 fully"), ahead of docs/13's original
wait-a-year pacing.

**How to apply:** dev-host reality — the live dev DB is the SYSTEM cluster
`17/main` on :5432 (`wolf` DB, user `wolf`), NOT a wolf-database-managed
cluster. Upgrading it is privileged (operator runs):
`sudo apt install postgresql-18-pgvector` FIRST (extension objects must
exist for the restore), stop wolf-server, `sudo pg_upgradecluster 17 main`,
verify (`pg_lsclusters`, `SELECT extversion FROM pg_extension WHERE
extname='vector'`), restart wolf-server, then `sudo pg_dropcluster 17 main`
+ remove the 17 packages. Related: [[embedding-stack-adr-0033]].
