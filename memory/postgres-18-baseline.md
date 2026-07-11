---
name: postgres-18-baseline
description: "PostgreSQL 18 fully replaced 17: gate REJECTS 17, packaging/CI/docs all 18; live dev cluster UPGRADED 2026-07-12 (18.4 + pgvector 0.8.5 on :5432, PG17 removed); default port 5432 everywhere (17860 = CI smoke only)"
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

**How to apply:** DONE on the dev host 2026-07-12 under an announced
temporary sudo grant (removed + verified after): pgvector-18 installed
FIRST, `pg_upgradecluster 17 main` → 18.4 + pgvector 0.8.5 live on :5432,
corpus verified identical (5182/5182/5182), 17 cluster dropped + packages
removed. The live dev DB remains the SYSTEM cluster (`wolf` DB, user
`wolf`), NOT wolf-database-managed. Ports: default 5432 everywhere;
`17860` appears ONLY in the CI smoke for collision avoidance. Related: [[embedding-stack-adr-0033]].
