"""CLI entry point: live two-organization isolation smoke suite.

Per doc 05 §Test isolation as a first-class, continuous practice:
"Don't treat 'organizations are isolated' as something verified once. Build
an automated cross-organization test suite that runs constantly... in CI
**and** as a synthetic probe in production."

This CLI is the "synthetic probe" path. It exercises the actual
deployed PgvectorKnowledgeStore + audit-log code paths against the
live dev DB (or any DB the operator points DATABASE_URL at), with
two known-distinct organizations seeded, and asserts that:

  1. Organization A's RAG retrieval returns ONLY acme + shared chunks
     (never beta's private content).
  2. Organization B's RAG retrieval returns ONLY beta + shared chunks.
  3. Audit writes for A do NOT appear in B's audit query.
  4. The OrganizationScopedCache wrapper refuses unprefixed keys.

Exit code 0 if every check passes; non-zero on any failure (the
caller — CI workflow or production-probe cron — reads this as a
binary green/red signal).

Run:
    cd services/server
    set -a && source ../../.env && set +a
    cd ../..
    uv run python -m tools.organization_isolation_test

The dev DB must already have `acme` and `beta` organizations bootstrapped
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
from wolf_server.caching import InMemoryOrganizationCache, UnprefixedKeyError
from wolf_server.caching.cache import _compose_storage_key
from wolf_server.config import get_settings
from wolf_server.database import db_session
from wolf_server.knowledge.embeddings import (
    make_embedding_provider,
    make_embedding_provider_aux,
)
from wolf_server.knowledge.models import KnowledgeChunk
from wolf_server.knowledge.store import PgvectorKnowledgeStore
from wolf_server.organization.models import Organization


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
    asker: Organization,
    other: Organization,
    store: PgvectorKnowledgeStore,
) -> Check:
    """As `asker`, run a query that would semantically match either
    organization's runbook content. Assert none of the returned chunks are
    owned by `other`."""
    hits = await store.search(
        organization_id=asker.id,
        query_text="SSH brute-force runbook steps",
        source_types=["runbook", "past_incident"],
        limit=10,
    )
    leaks = [
        h.chunk_metadata.get("title", str(h.id)) for h in hits if h.organization_id == other.id
    ]
    if leaks:
        return Check(
            label,
            False,
            f"LEAKED chunks owned by {other.slug!r}: {leaks}",
        )
    return Check(
        label,
        True,
        f"{len(hits)} hits returned, all owned by {asker.slug} or shared",
    )


async def check_audit_isolation(asker: Organization, other: Organization) -> Check:
    """Write an audit event as `asker`, query as `other`, assert empty."""
    event_marker = f"isolation-probe-{uuid.uuid4().hex[:8]}"
    async with db_session() as s:
        await write_event(
            s,
            event_type=event_marker,
            event_data={"probe": True, "asker": asker.slug},
            organization_id=asker.id,
        )
        await s.commit()

    async with db_session() as s:
        other_rows = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.organization_id == other.id,
                        AuditEvent.event_type == event_marker,
                    )
                )
            )
            .scalars()
            .all()
        )
    if other_rows:
        return Check(
            "audit write isolation",
            False,
            f"LEAKED: {other.slug!r}'s organization-scoped audit query "
            f"returned {len(other_rows)} rows written by {asker.slug!r}",
        )
    return Check(
        "audit write isolation",
        True,
        f"event {event_marker!r} from {asker.slug} not visible to {other.slug}",
    )


def check_cache_unprefixed_rejected() -> Check:
    try:
        _compose_storage_key(None, "ns", "k")  # type: ignore[arg-type]
    except UnprefixedKeyError:
        return Check(
            "cache rejects unprefixed key",
            True,
            "UnprefixedKeyError raised as designed",
        )
    return Check(
        "cache rejects unprefixed key",
        False,
        "UnprefixedKeyError was NOT raised — the wrapper's organization-prefix "
        "enforcement is broken",
    )


async def check_cache_cross_organization_isolation() -> Check:
    cache = InMemoryOrganizationCache()
    a = uuid.uuid4()
    b = uuid.uuid4()
    await cache.set(a, "ns", "k", "value-from-a")
    if await cache.get(b, "ns", "k") is not None:
        return Check(
            "cache cross-organization isolation",
            False,
            "LEAK: organization B's get satisfied by organization A's cached entry",
        )
    return Check(
        "cache cross-organization isolation",
        True,
        "organization B sees a miss for organization A's cached entry",
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Live two-organization isolation smoke suite. Exit 0 on full pass; "
            "non-zero on any check failure (CI / production-probe consumers "
            "read this as a binary signal)."
        ),
    )
    parser.add_argument(
        "--organization-a",
        default="acme",
        help="Slug of the first organization (default 'acme').",
    )
    parser.add_argument(
        "--organization-b",
        default="beta",
        help="Slug of the second organization (default 'beta').",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set. Source .env first:\n"
            "    set -a && source ../../.env && set +a\n"
        )
        return 2

    settings = get_settings()

    # Load both organizations.
    async with db_session() as s:
        rows = (
            (
                await s.execute(
                    select(Organization).where(
                        Organization.slug.in_([args.organization_a, args.organization_b])
                    )
                )
            )
            .scalars()
            .all()
        )
    organizations = {t.slug: t for t in rows}
    missing = [
        slug for slug in (args.organization_a, args.organization_b) if slug not in organizations
    ]
    if missing:
        sys.stderr.write(
            f"ERROR: missing organization(s): {missing}. Bootstrap them first via "
            f"`uv run python -m wolf_server.management.bootstrap_organization ...`.\n"
        )
        return 3

    a = organizations[args.organization_a]
    b = organizations[args.organization_b]

    # Verify each organization has at least one private chunk so the probe is
    # meaningful (a organization with zero private chunks can't be leaked from).
    async with db_session() as s:
        a_private = (
            await s.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.organization_id == a.id).limit(1)
            )
        ).scalar_one_or_none()
        b_private = (
            await s.execute(
                select(KnowledgeChunk).where(KnowledgeChunk.organization_id == b.id).limit(1)
            )
        ).scalar_one_or_none()
    if a_private is None or b_private is None:
        sys.stderr.write(
            "ERROR: one or both organizations have no private chunks. Run "
            "`uv run python -m wolf_server.management.seed_dev_knowledge "
            f"--organization-slug {args.organization_a}` (and again for the other) "
            "before this probe.\n"
        )
        return 4

    print(
        f"Probing organizations {args.organization_a!r} (id={a.id}) vs "
        f"{args.organization_b!r} (id={b.id})"
    )

    checks: list[Check] = []

    # RAG checks — both directions, against the same retrieval pipeline
    # the live chat path uses (single-leg or chained, whatever's
    # configured via EMBEDDING_MODEL_AUX).
    primary = make_embedding_provider(settings)
    aux = make_embedding_provider_aux(settings)
    async with db_session() as s_a:
        store_a = PgvectorKnowledgeStore(s_a, primary, embedder_aux=aux)
        checks.append(
            await check_rag_isolation(
                f"RAG: {args.organization_a} cannot see {args.organization_b}'s chunks",
                a,
                b,
                store_a,
            )
        )
    async with db_session() as s_b:
        store_b = PgvectorKnowledgeStore(s_b, primary, embedder_aux=aux)
        checks.append(
            await check_rag_isolation(
                f"RAG: {args.organization_b} cannot see {args.organization_a}'s chunks",
                b,
                a,
                store_b,
            )
        )

    # Audit checks — both directions.
    checks.append(await check_audit_isolation(a, b))
    checks.append(await check_audit_isolation(b, a))

    # Cache checks — no DB needed.
    checks.append(check_cache_unprefixed_rejected())
    checks.append(await check_cache_cross_organization_isolation())

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
