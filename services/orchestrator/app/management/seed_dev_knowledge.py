"""seed_dev_knowledge — load a tiny inline corpus for Phase 3 Slice 1 dev.

Slice-1 scope: ~10 hand-written chunks covering one Wazuh rule (5710 — SSH
brute force) and one ATT&CK technique (T1110 — Brute Force) plus a couple
of tenant-private runbook chunks. Just enough to exercise the full vertical
(chat → query_runbook → vector retrieval → cited answer) and to give the
cross-tenant isolation test real content to discriminate on.

Slice-3 replaces this with real scrapers in tools/seed_knowledge.

Usage:
    set -a && source .env && set +a
    cd services/orchestrator
    uv run python -m app.management.seed_dev_knowledge --tenant-slug acme

Idempotent only in the trivial sense: re-running inserts a fresh copy of
every seed chunk. To reset, delete the rows and re-run. (A real seed CLI
in Slice 3 will diff by content hash; this dev seed is too small to bother.)
"""

import argparse
import asyncio
import json
import os
import sys

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.database import db_session
from app.knowledge.embeddings import OllamaEmbeddingAdapter
from app.knowledge.store import ChunkInput, PgvectorKnowledgeStore
from app.tenancy.models import Tenant

logger = structlog.get_logger(__name__)


# Shared corpora (visible to every tenant) — tenant_id=None.
SHARED_CHUNKS: list[tuple[str, dict, str]] = [
    (
        "wazuh_doc",
        {"rule_id": "5710", "title": "Rule 5710 — sshd authentication failure"},
        "Wazuh rule 5710 fires when sshd logs an authentication failure for "
        "an invalid user. Default level is 5. Defined in ruleset file "
        "0095-sshd_rules.xml. Common parent of higher-severity SSH "
        "brute-force composite rules (5712 at level 10 for repeated "
        "failures from the same source).",
    ),
    (
        "wazuh_doc",
        {"rule_id": "5712", "title": "Rule 5712 — SSH brute-force composite"},
        "Wazuh rule 5712 is a composite rule that fires when rule 5710 "
        "triggers 8 or more times from the same source IP within 120 "
        "seconds. Default level is 10. To tune the threshold, override "
        "the rule in local_rules.xml and adjust the frequency/timeframe "
        "options. Common false-positive source: misconfigured monitoring "
        "agents repeatedly polling an SSH port.",
    ),
    (
        "wazuh_doc",
        {"title": "Active response — disable_account"},
        "Wazuh's active-response framework can run scripts on agents in "
        "response to alerts. The built-in disable_account command (for "
        "Linux) calls passwd -l on the offending account. Configured in "
        "ossec.conf under <command> + <active-response>. Always pair with "
        "<timeout> for automatic re-enablement; permanent lockouts via "
        "active response are a common operator footgun.",
    ),
    (
        "attack",
        {"technique": "T1110", "title": "T1110 — Brute Force"},
        "ATT&CK technique T1110 (Brute Force): adversaries may use brute "
        "force techniques to gain access to accounts when passwords are "
        "unknown or when password hashes are obtained. Sub-techniques "
        "include T1110.001 Password Guessing, T1110.003 Password Spraying, "
        "T1110.004 Credential Stuffing. Detection: failed authentication "
        "logs, unusual login source IPs, anomalous login times.",
    ),
    (
        "attack",
        {"technique": "T1110.001", "title": "T1110.001 — Password Guessing"},
        "ATT&CK sub-technique T1110.001 (Password Guessing): an adversary "
        "without knowledge of valid credentials may guess login credentials "
        "without prior knowledge of system or environment passwords during "
        "an operation. SSH and RDP are common targets. Wazuh rule 5712 is "
        "directly relevant for the SSH variant.",
    ),
    (
        "attack",
        {"technique": "T1078", "title": "T1078 — Valid Accounts"},
        "ATT&CK technique T1078 (Valid Accounts): adversaries may obtain "
        "and abuse credentials of existing accounts as a means of gaining "
        "Initial Access, Persistence, Privilege Escalation, or Defense "
        "Evasion. Compromised credentials may be used to bypass access "
        "controls placed on various resources on systems within the "
        "network.",
    ),
]


# Tenant-private chunks — these are seeded under the named tenant only.
# The cross-tenant isolation test seeds the other tenant with different
# content so a leak is observable.
def runbook_chunks_for(tenant_slug: str) -> list[tuple[str, dict, str]]:
    return [
        (
            "runbook",
            {
                "rule_id": "5712",
                "title": f"{tenant_slug.upper()} SOC — SSH brute-force response",
            },
            f"[{tenant_slug.upper()} SOC] SSH brute-force runbook (rule 5712):\n"
            "1. Confirm the agent is reachable via list_agents.\n"
            "2. Use get_event_timeline to gather all 5710/5712 events "
            "from the source IP in the last hour.\n"
            "3. If the source IP is external and non-business: block at "
            f"the perimeter firewall (escalate to network ops for "
            f"{tenant_slug.upper()}).\n"
            "4. If the source IP is internal: open a P2 ticket and "
            "investigate which host is initiating the connections.\n"
            "5. Never auto-disable_account on rule 5712 alone — too many "
            "false positives from monitoring agents.",
        ),
        (
            "runbook",
            {
                "technique": "T1110",
                "title": f"{tenant_slug.upper()} SOC — Brute-force triage",
            },
            f"[{tenant_slug.upper()} SOC] T1110 triage guidance: prioritize "
            "by source-IP reputation, target account sensitivity, and "
            "presence of subsequent T1078 (Valid Accounts) signals on the "
            "same agent. A successful brute force followed by lateral "
            "movement is a P1; an unsuccessful sweep with no follow-on "
            "activity is typically P3.",
        ),
        (
            "past_incident",
            {"title": f"INC-2026-0042 — {tenant_slug.upper()} SSH sweep"},
            f"[{tenant_slug.upper()} past incident INC-2026-0042, "
            "2026-04-12]: external IP 198.51.100.42 ran SSH brute force "
            "against jump-host bastion-03 for 2 hours, triggering 47 "
            "instances of rule 5712. No accounts were compromised "
            "(strong passwords + key-only auth). Action taken: perimeter "
            "block. Lesson: confirm key-only auth posture during the "
            "first triage step; reduces investigation time substantially.",
        ),
    ]


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed Wolf's knowledge corpora with a tiny dev set."
    )
    parser.add_argument(
        "--tenant-slug",
        required=True,
        help="Slug of the tenant whose private corpus will receive the runbook chunks.",
    )
    args = parser.parse_args()

    # Conftest-style env defaults if .env wasn't sourced — fail loud
    # rather than silently using SQLite for a Postgres-only seed.
    if not os.environ.get("DATABASE_URL"):
        sys.stderr.write(
            "ERROR: DATABASE_URL is not set. Source .env first:\n"
            "    set -a && source ../../.env && set +a\n"
        )
        return 2

    settings = get_settings()
    embedder = OllamaEmbeddingAdapter(settings.ollama_base_url)

    async with db_session() as session:
        tenant_q = await session.execute(
            select(Tenant).where(Tenant.slug == args.tenant_slug)
        )
        tenant = tenant_q.scalar_one_or_none()
        if tenant is None:
            sys.stderr.write(
                f"ERROR: No tenant with slug={args.tenant_slug!r}. "
                f"Bootstrap one first.\n"
            )
            return 3

        store = PgvectorKnowledgeStore(session, embedder)
        # Shared corpora — tenant_id=None.
        shared_inputs = [
            ChunkInput(
                content=content,
                source_type=source_type,
                tenant_id=None,
                chunk_metadata=metadata,
            )
            for source_type, metadata, content in SHARED_CHUNKS
        ]
        # Tenant-private corpora — tenant_id set.
        private_inputs = [
            ChunkInput(
                content=content,
                source_type=source_type,
                tenant_id=tenant.id,
                chunk_metadata=metadata,
            )
            for source_type, metadata, content in runbook_chunks_for(
                args.tenant_slug
            )
        ]
        all_inputs = shared_inputs + private_inputs
        ids = await store.upsert(all_inputs)
        result = {
            "tenant_slug": args.tenant_slug,
            "tenant_id": str(tenant.id),
            "shared_chunks_added": len(shared_inputs),
            "private_chunks_added": len(private_inputs),
            "chunk_ids": [str(i) for i in ids],
        }
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
