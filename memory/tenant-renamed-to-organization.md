---
name: tenant-renamed-to-organization
description: "STANDING RULE (2026-06-10) — refer to tenants as Organizations EVERYWHERE going forward (frontend, backend, database, docs, conversation). The codebase rename is a planned slice; new ADRs + memories use \"organization\" from now on."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Cross-ref (2026-06-11):** this rename is now **scheduled as Phase 6.4** in the roadmap — the unblocked pre-req for all Phase 6.5+ work. See [ADR 0018 — Bootstrap Superuser + Per-Org RBAC + Login UX](../docs/decisions/0018-bootstrap-superuser-rbac-login.md) §"Implementation sequencing" → "Pre-requisite: Phase 6.4". The full rename matrix is captured below; Phase 6.4 is a single PR (~40-60 files, 1-2 sessions). This memory entry FLIPS FROM STANDING RULE TO COMPLETED at the end of Phase 6.4 — `tenant-renamed-to-organization` will then be a historical record, not an active rule.

STANDING RULE (2026-06-10): the operator made the explicit decision that **tenant = organization** is the canonical naming. There is no separate Organization entity; the existing Tenant model IS the org. Going forward:

## Naming convention (use these everywhere)

| Old name | New name | Where applies |
|---|---|---|
| Tenant (class / concept) | **Organization** | Python models, TS types, conversation |
| tenant_id (column / variable) | **organization_id** (or `org_id` if a shorter form is needed) | SQL schema, Pydantic models, FastAPI route params, TS types |
| tenants (table) | **organizations** | Postgres schema |
| `/api/v1/tenants/*` | **`/api/v1/organizations/*`** | FastAPI routes |
| tenant-switcher.tsx | **organization-switcher.tsx** | React component |
| Cross-tenant test suite | **Cross-organization isolation suite** | `tools/cross_organization_isolation/`, pytest file names, CI job name |
| TenantContext | **OrganizationContext** | The load-bearing isolation primitive |
| UserTenant (membership) | **UserOrganization** | Many-to-many membership table |

## Code-side status

The CODEBASE itself has NOT yet been renamed. The rename is a planned slice — substantial refactor touching ~40-60 files. Until that slice executes:

- The actual model is still `Tenant`, the column is still `tenant_id`, etc.
- Reading the code, you'll see `tenant` everywhere.
- This is a LEGACY FACT, not the target state. The rename slice fixes it.

## In docs / ADRs / new memories

- Use **organization** everywhere from 2026-06-10 onward.
- ADR 0017 (Wolf Central Brain) — being updated in the same commit chain that lands this memory entry.
- ADR 0018+ (RBAC + bootstrap + login UX + Wazuh-mapping) — written using organization from the start.
- ADR 0019+ (web-first configurability) — same.
- New roadmap entries — same.

## In conversation with the operator

- When the user says "tenant," they mean "organization." I treat the two as synonymous.
- When I write to the user, I use **organization**.
- If I need to reference the legacy code, I might say "the `Tenant` model in `tenancy/models.py` (the codebase still uses the old name until the rename slice)" for clarity.

## The rename slice itself

When the operator opens the rename slice, the work is:

1. Alembic migration:
   - `RENAME TABLE tenants TO organizations`
   - `RENAME COLUMN tenant_id TO organization_id` on every table that has it (audit_events, knowledge_chunks, conversations, secrets, etc.)
   - `RENAME TABLE user_tenants TO user_organizations`
   - Foreign keys + indexes follow the rename
2. SQLAlchemy models: `Tenant` → `Organization`, `UserTenant` → `UserOrganization`, every column rename
3. Pydantic schemas + TS types
4. API routes (`/api/v1/tenants` → `/api/v1/organizations`); maintain `/api/v1/tenants/*` as a deprecated alias for one release for backwards compat
5. React components + hooks + state names
6. ALL docs (decisions/, planning bundle docs/00-17, ONBOARDING, SECURITY, SUPPORT, RELEASING, CHANGELOG, PROGRESS)
7. Cross-organization isolation test suite (rename files, update imports, update CI job name)
8. CLI tools: `bootstrap_tenant.sh` → `bootstrap_organization.sh`
9. Audit-log event names

Estimated scope: 1-2 sessions. **Recommended to land BEFORE Phase 7.5** (Wolf Central Brain) opens, so the new memory + brain schema is written with the correct name from the start.

## Why this rule is here (not just "obvious")

Without an explicit rule, I'd default to using whatever the code currently says (tenant). That means new docs + ADRs would use "tenant," compounding the rename debt. This rule forces forward-looking writing to use the right name even before the code rename lands.

Related: [[wolf-bootstrap-superuser-flow]], [[integrity-across-the-stack]] (the rename must preserve isolation invariants), [[graphify-first-discipline]] (after the rename slice lands, graphify rebuild will reflect the new names).
