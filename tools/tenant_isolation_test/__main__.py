"""CLI entry point: live two-tenant isolation smoke suite.

Per doc 05 §Test isolation as a first-class, continuous practice:
"Don't treat 'tenants are isolated' as something verified once. Build
an automated cross-tenant test suite that runs constantly... in CI
**and** as a synthetic probe in production."

This CLI is the "synthetic probe" path. It exercises the actual
deployed PgvectorKnowledgeStore + audit-log code paths against the
live dev DB (or any DB the operator points DATABASE_URL at), with
two known-distinct tenants seeded, and asserts that:

  1. Tenant A's RAG retrieval returns ONLY acme + shared chunks
     (never beta's private content).
  2. Tenant B's RAG retrieval returns ONLY beta + shared chunks.
  3. Audit writes for A do NOT appear in B's audit query.
  4. The TenantScopedCache wrapper refuses unprefixed keys.

Exit code 0 if every check passes; non-zero on any failure (the
caller — CI workflow or production-probe cron — reads this as a
binary green/red signal).

Run:
    cd services/server
    set -a && source ../../.env && set +a
    cd ../..
    uv run python -m tools.tenant_isolation_test

The dev DB must already have `acme` and `beta` tenants bootstrapped
(Phase 4 Slice 1's setup). The suite is read-only in spirit — it
writes its own audit events with a "isolation-probe" event_type
prefix, but never deletes or modifies existing data.
"""

# ruff: noqa: T201

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from wolf_server.audit.log import write_event
from wolf_server.audit.models import AuditEvent
from wolf_server.caching import InMemoryTenantCache, UnprefixedKeyError
from wolf_server.caching.cache import _compose_storage_key
from wolf_server.config import get_settings
from wolf_server.database import db_session
from wolf_server.knowledge.embeddings import (
    make_embedding_provider,
    make_embedding_provider_aux,
)
from wolf_server.knowledge.models import KnowledgeChunk
from wolf_server.knowledge.store import PgvectorKnowledgeStore
from wolf_server.tenancy.models import Tenant


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def format(self) -> str:
        marker = "✓" if self.passed else "✗"
        return f"  {marker} {self.name}: {self.detail}"


async def check_rag_isolation(
    label: str,
    asker: Tenant,
    other: Tenant,
    store: PgvectorKnowledgeStore,
) -> Check:
    """As `asker`, run a query that would semantically match either
    tenant's runbook content. Assert none of the returned chunks are
    owned by `other`."""
    hits = await store.search(
        tenant_id=asker.id,
        query_text="SSH brute-force runbook steps",
        source_types=["runbook", "past_incident"],
        limit=10,
    )
    leaks = [
        h.chunk_metadata.get("title", str(h.id))
        for h in hits
        if h.tenant_id == other.id
    ]
    if leaks:
        return Check(
            label, False,
            f"LEAKED chunks owned by {other.slug!r}: {leaks}",
        )
    return Check(
        label, True,
        f"{len(hits)} hits returned, all owned by {asker.slug} or shared",
    )


async def check_audit_isolation(asker: Tenant, other: Tenant) -> Check:
    """Write an audit event as `asker`, query as `other`, assert empty."""
    event_marker = f"isolation-probe-{uuid.uuid4().hex[:8]}"
    async with db_session() as s:
        await write_event(
            s, event_type=event_marker,
            event_data={"probe": True, "asker": asker.slug},
            tenant_id=asker.id,
        )
        await s.commit()

    async with db_session() as s:
        other_rows = (
            await s.execute(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == other.id,
                    AuditEvent.event_type == event_marker,
                )
            )
        ).scalars().all()
    if other_rows:
        return Check(
            "audit write isolation", False,
            f"LEAKED: {other.slug!r}'s tenant-scoped audit query "
            f"returned {len(other_rows)} rows written by {asker.slug!r}",
        )
    return Check(
        "audit write isolation", True,
        f"event {event_marker!r} from {asker.slug} not visible to {other.slug}",
    )


def check_cache_unprefixed_rejected() -> Check:
    try:
        _compose_storage_key(None, "ns", "k")  # type: ignore[arg-type]
    except UnprefixedKeyError:
        return Check(
            "cache rejects unprefixed key", True,
            "UnprefixedKeyError raised as designed",
        )
    return Check(
        "cache rejects unprefixed key", False,
        "UnprefixedKeyError was NOT raised — the wrapper's tenant-prefix "
        "enforcement is broken",
    )


async def check_cache_cross_tenant_isolation() -> Check:
    cache = InMemoryTenantCache()
    a = uuid.uuid4()
    b = uuid.uuid4()
    await cache.set(a, "ns", "k", "value-from-a")
    if await cache.get(b, "ns", "k") is not None:
        return Check(
            "cache cross-tenant isolation", False,
            "LEAK: tenant B's get satisfied by tenant A's cached entry",
        )
    return Check(
        "cache cross-tenant isolation", True,
        "tenant B sees a miss for tenant A's cached entry",
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Live two-tenant isolation smoke suite. Exit 0 on full pass; "
            "non-zero on any check failure (CI / production-probe consumers "
            "read this as a binary signal)."
        ),
    )
    parser.add_argument(
        "--tenant-a", default="acme",
        help="Slug of the first tenant (default 'acme').",
    )
    parser.add_argument(
        "--tenant-b", default="beta",
        help="Slug of the second tenant (default 'beta').",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set. Source .env first:\n"
            "    set -a && source ../../.env && set +a\n"
        )
        return 2

    settings = get_settings()

    # Load both tenants.
    async with db_session() as s:
        rows = (
            await s.execute(
                select(Tenant).where(
                    Tenant.slug.in_([args.tenant_a, args.tenant_b])
                )
            )
        ).scalars().all()
    tenants = {t.slug: t for t in rows}
    missing = [
        slug for slug in (args.tenant_a, args.tenant_b) if slug not in tenants
    ]
    if missing:
        sys.stderr.write(
            f"ERROR: missing tenant(s): {missing}. Bootstrap them first via "
            f"`uv run python -m wolf_server.management.bootstrap_tenant ...`.\n"
        )
        return 3

    a = tenants[args.tenant_a]
    b = tenants[args.tenant_b]

    # Verify each tenant has at least one private chunk so the probe is
    # meaningful (a tenant with zero private chunks can't be leaked from).
    async with db_session() as s:
        a_private = (
            await s.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.tenant_id == a.id).limit(1)
            )
        ).scalar_one_or_none()
        b_private = (
            await s.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.tenant_id == b.id).limit(1)
            )
        ).scalar_one_or_none()
    if a_private is None or b_private is None:
        sys.stderr.write(
            "ERROR: one or both tenants have no private chunks. Run "
            "`uv run python -m wolf_server.management.seed_dev_knowledge "
            f"--tenant-slug {args.tenant_a}` (and again for the other) "
            "before this probe.\n"
        )
        return 4

    print(
        f"Probing tenants {args.tenant_a!r} (id={a.id}) vs "
        f"{args.tenant_b!r} (id={b.id})"
    )

    checks: list[Check] = []

    # RAG checks — both directions, against the same retrieval pipeline
    # the live chat path uses (single-leg or chained, whatever's
    # configured via EMBEDDING_MODEL_AUX).
    primary = make_embedding_provider(settings)
    aux = make_embedding_provider_aux(settings)
    async with db_session() as s_a:
        store_a = PgvectorKnowledgeStore(s_a, primary, embedder_aux=aux)
        checks.append(await check_rag_isolation(
            f"RAG: {args.tenant_a} cannot see {args.tenant_b}'s chunks",
            a, b, store_a,
        ))
    async with db_session() as s_b:
        store_b = PgvectorKnowledgeStore(s_b, primary, embedder_aux=aux)
        checks.append(await check_rag_isolation(
            f"RAG: {args.tenant_b} cannot see {args.tenant_a}'s chunks",
            b, a, store_b,
        ))

    # Audit checks — both directions.
    checks.append(await check_audit_isolation(a, b))
    checks.append(await check_audit_isolation(b, a))

    # Cache checks — no DB needed.
    checks.append(check_cache_unprefixed_rejected())
    checks.append(await check_cache_cross_tenant_isolation())

    # Report.
    print("\nResults:")
    for c in checks:
        print(c.format())

    fails = [c for c in checks if not c.passed]
    print()
    if fails:
        print(f"FAIL — {len(fails)}/{len(checks)} checks failed.")
        return 1
    print(f"PASS — {len(checks)}/{len(checks)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
