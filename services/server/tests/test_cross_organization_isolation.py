"""Cross-organization isolation tests for Phase 2A read path.

Per doc 05: as Organization A, attempts to read Organization B's data must **fail closed**.
These tests cover the negative cases for the read tools/clients delivered in
Phase 2A, plus the Phase 6 (ADR 0025) action-proposal queue — which is
org-scoped and forced-filtered exactly like every other per-org table.

Run in CI on every PR (see `tools/organization_isolation_test/` for the canonical
home; this file is the implementation that the test job runs).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from wolf_common.errors import OrganizationMismatchError
from wolf_server.wazuh.config import WazuhConnection
from wolf_server.wazuh.opensearch import WazuhOpenSearchClient
from wolf_server.wazuh.query_builder import OrganizationScopedQueryBuilder

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _label_for(organization_id: uuid.UUID) -> str:
    """A distinct agent.labels.group value per organization."""
    return f"org-{organization_id}"


def _connection_for(organization_id: uuid.UUID) -> WazuhConnection:
    return WazuhConnection(
        organization_id=organization_id,
        opensearch_url="https://os.example.test:9200",
        opensearch_index_pattern="wazuh-alerts-*",
        opensearch_username=f"organization-{organization_id}-ro",
        opensearch_password="secret",  # noqa: S106 — test fixture
        server_api_url="https://api.example.test:55000",
        server_api_username=f"organization-{organization_id}-api",
        server_api_password="secret",  # noqa: S106 — test fixture
        verify_tls=True,
        inject_group_label_filter=True,
        agent_group_labels=(_label_for(organization_id),),
    )


# ─── Test: query builders for different organizations do not produce equal queries ─


def test_two_organization_builders_with_filter_produce_distinct_queries() -> None:
    """With the group-label filter on, each org's queries carry its own label."""
    a = OrganizationScopedQueryBuilder(
        uuid.uuid4(), inject_group_label_filter=True, agent_group_labels=["org-a"]
    )
    b = OrganizationScopedQueryBuilder(
        uuid.uuid4(), inject_group_label_filter=True, agent_group_labels=["org-b"]
    )
    now = datetime.now(UTC)
    qa = a.search_alerts(time_from=now - timedelta(hours=1), time_to=now)
    qb = b.search_alerts(time_from=now - timedelta(hours=1), time_to=now)
    # The two queries differ in the terms:{agent.labels.group} clause.
    assert qa != qb


# ─── Test: organization-A OpenSearch client rejects a organization-B-built query ─────────


@pytest.mark.asyncio
async def test_organization_a_client_rejects_organization_b_query() -> None:
    """A query carrying Organization B's label cannot run on Organization A's client."""
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()

    builder_b = OrganizationScopedQueryBuilder(
        organization_b,
        inject_group_label_filter=True,
        agent_group_labels=[_label_for(organization_b)],
    )
    now = datetime.now(UTC)
    bad_query = builder_b.search_alerts(time_from=now - timedelta(hours=1), time_to=now)

    async def _never(_req: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "Organization A must reject organization B's query before any HTTP call"
        )

    http = httpx.AsyncClient(
        base_url="https://os.example.test:9200",
        transport=httpx.MockTransport(_never),
        timeout=5.0,
    )
    client = WazuhOpenSearchClient(_connection_for(organization_a), client=http)
    with pytest.raises(OrganizationMismatchError, match="agent.labels.group filter"):
        await client.execute(bad_query)


# ─── Test: organization-A client rejects a returned doc tagged for organization B ────────


@pytest.mark.asyncio
async def test_organization_a_client_rejects_returned_organization_b_doc() -> None:
    """If a query somehow returns a doc with Organization B's group label, fail closed."""
    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()

    async def _handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "leaked",
                        "_source": {
                            "agent": {"labels": {"group": _label_for(organization_b)}},
                            "rule": {"id": "5710"},
                        },
                    }
                ],
            }
        }
        return httpx.Response(200, json=body, request=request)

    http = httpx.AsyncClient(
        base_url="https://os.example.test:9200",
        transport=httpx.MockTransport(_handler),
        timeout=5.0,
    )
    client = WazuhOpenSearchClient(_connection_for(organization_a), client=http)
    now = datetime.now(UTC)
    good_query = client.query_builder.search_alerts(time_from=now - timedelta(hours=1), time_to=now)
    with pytest.raises(OrganizationMismatchError, match="agent.labels.group"):
        await client.execute(good_query)


# ─── Test: audit reads scoped to organization A do not see organization B's events ───────


@pytest.mark.asyncio
async def test_audit_reads_are_organization_scoped(db: Any) -> None:
    """Phase-0 invariant re-verified: audit queries filter by organization_id."""
    from sqlalchemy import select
    from wolf_server.audit.log import write_event
    from wolf_server.audit.models import AuditEvent

    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()

    await write_event(
        db,
        event_type="test.cross_organization.a",
        event_data={"who": "a"},
        organization_id=organization_a,
    )
    await write_event(
        db,
        event_type="test.cross_organization.b",
        event_data={"who": "b"},
        organization_id=organization_b,
    )
    await db.commit()

    a_rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.organization_id == organization_a)))
        .scalars()
        .all()
    )
    assert all(r.event_data["who"] == "a" for r in a_rows)
    assert all(r.organization_id == organization_a for r in a_rows)

    b_rows = (
        (await db.execute(select(AuditEvent).where(AuditEvent.organization_id == organization_b)))
        .scalars()
        .all()
    )
    assert all(r.event_data["who"] == "b" for r in b_rows)
    assert all(r.organization_id == organization_b for r in b_rows)


# ─── Test: PgvectorKnowledgeStore.search() builds organization-scoped SQL ──────────
#
# Phase 3 added the RAG path; doc 05 demands the same isolation discipline
# for retrieval. The store's SQL clause is the load-bearing enforcement
# point — the unit tests below verify it's wired correctly without
# requiring a live Postgres. End-to-end isolation against the dev
# corpus is covered by tools/organization_isolation_test (Phase 4 Slice 4).


def test_pgvector_store_search_constrains_results_to_requesting_organization() -> None:
    """The store's leg-helpers must include WHERE organization_id IS NULL OR
    organization_id = $req in every candidate query. No 'select all chunks
    and filter in Python' path can exist."""
    import inspect

    from wolf_server.knowledge.store import PgvectorKnowledgeStore

    # Source-level invariant: every candidate-fetcher method must
    # construct a where clause that scopes by organization_id. We assert the
    # presence of the organization-scoping predicate text rather than running
    # SQL — a future contributor would have to delete the predicate to
    # break isolation, which the source-level check catches.
    for helper_name in (
        "_vector_candidates",
        "_fts_candidates",
        "_vector_aux_candidates",
    ):
        source = inspect.getsource(getattr(PgvectorKnowledgeStore, helper_name))
        assert "organization_id.is_(None)" in source, (
            f"{helper_name} missing shared-corpora clause (organization_id IS NULL)"
        )
        assert "organization_id == organization_id" in source, (
            f"{helper_name} missing requesting-organization clause "
            f"(organization_id == :req_organization)"
        )


def test_pgvector_chunk_input_validation_blocks_cross_organization_writes() -> None:
    """A shared-corpus chunk MUST have organization_id=None; a organization-private
    chunk MUST have a organization_id. The store's validate-on-upsert prevents
    accidental cross-organization writes at the data layer."""
    from wolf_server.knowledge.store import ChunkInput, PgvectorKnowledgeStore

    # Shared corpus with a organization_id is a configuration mistake that
    # would let that organization's content leak to every other organization.
    bad_shared = ChunkInput(
        content="leaking content",
        source_type="wazuh_doc",
        organization_id=uuid.uuid4(),
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="organization_id must be None"):
        PgvectorKnowledgeStore._validate_chunk(bad_shared)

    # Organization-private corpus without a organization_id would also be a leak —
    # the chunk would appear in every organization's retrieval (since the
    # search WHERE clause matches organization_id IS NULL too).
    bad_private = ChunkInput(
        content="leaking runbook",
        source_type="runbook",
        organization_id=None,
        chunk_metadata={},
    )
    with pytest.raises(ValueError, match="organization_id is required"):
        PgvectorKnowledgeStore._validate_chunk(bad_private)


# ─── Test: audit WRITES never bleed into another organization's organization_id ─────────
#
# Doc 05 §The audit stream: "Every audit record is organization-tagged at write
# time, and audit reads are themselves organization-scoped." The existing test
# above covers READ isolation; this one covers WRITE isolation — the
# audit-stream is not exempt from isolation just because it's
# infrastructure.


@pytest.mark.asyncio
async def test_audit_writes_stamp_organization_id_at_write_time(db: Any) -> None:
    """A write for organization A must persist with organization_a in the row, not
    bleed under organization_b's id even if the audit_data payload happens to
    reference organization_b. The write path takes organization_id as a positional
    argument and the row stores it directly."""
    from sqlalchemy import select
    from wolf_server.audit.log import write_event
    from wolf_server.audit.models import AuditEvent

    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()

    # Adversarial payload: organization_a writes an event whose data field
    # mentions organization_b. The stored row's organization_id must be organization_a
    # regardless of payload content.
    await write_event(
        db,
        event_type="test.adversarial_payload",
        event_data={
            "narrative": "this row is for organization_a",
            "mentions_other_organization": str(organization_b),
            "fake_organization_id": str(organization_b),  # tries to confuse the row
        },
        organization_id=organization_a,
    )
    await db.commit()

    rows = (
        (
            await db.execute(
                select(AuditEvent).where(AuditEvent.event_type == "test.adversarial_payload")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    # The COLUMN stamps organization_a — the payload doesn't influence the
    # column. Organization B reading their audit stream would not see this row.
    assert rows[0].organization_id == organization_a
    assert rows[0].organization_id != organization_b
    # The payload preserves the (deliberately misleading) text; the
    # row's column is what matters for isolation.
    assert rows[0].event_data["fake_organization_id"] == str(organization_b)


@pytest.mark.asyncio
async def test_pgvector_search_call_path_includes_requesting_organization_id() -> None:
    """Sanity-check the call shape: search() forwards the requesting
    organization_id to every leg's helper. A regression would silently break
    isolation; the unit test catches it without needing a live DB."""
    from unittest.mock import AsyncMock, patch

    from wolf_server.knowledge.store import PgvectorKnowledgeStore

    class _StubEmbedder:
        model_id = "stub"
        dimension = 768

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 768 for _ in texts]

    class _StubSession:
        async def execute(self, _stmt: Any) -> Any:
            class _Result:
                @staticmethod
                def all() -> list[tuple[Any, float]]:
                    return []

            return _Result()

    store = PgvectorKnowledgeStore(_StubSession(), _StubEmbedder())
    req_organization = uuid.uuid4()
    other_organization = uuid.uuid4()
    assert req_organization != other_organization

    with (
        patch.object(store, "_vector_candidates", AsyncMock(return_value={})) as vec_mock,
        patch.object(store, "_fts_candidates", AsyncMock(return_value={})) as fts_mock,
    ):
        await store.search(organization_id=req_organization, query_text="x", limit=5)

    # Both legs receive the REQUESTING organization_id, never something else.
    vec_kwargs_or_args = vec_mock.call_args
    fts_kwargs_or_args = fts_mock.call_args
    # Both helpers are called positionally with (organization_id, ...) as the first arg.
    assert vec_kwargs_or_args.args[0] == req_organization
    assert fts_kwargs_or_args.args[0] == req_organization
    # And NOT with the other organization's id.
    assert other_organization not in vec_kwargs_or_args.args
    assert other_organization not in fts_kwargs_or_args.args


# ─── Test: action proposals are organization-scoped (Phase 6, ADR 0025) ──────────
#
# The approval queue is per-org data; a proposal created for organization A must
# be invisible to a query scoped to organization B (the forced organization_id
# filter in api/action_proposals._load_proposal + list_proposals).


@pytest.mark.asyncio
async def test_action_proposals_are_organization_scoped(db: Any) -> None:
    """A proposal for organization A is invisible to organization B's forced-filtered query."""
    from sqlalchemy import select
    from wolf_server.gateway.models import ActionProposal
    from wolf_server.gateway.proposals import create_proposal

    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()

    await create_proposal(
        db,
        organization_id=organization_a,
        requested_by=uuid.uuid4(),
        action_class="active_response",
        target={"agent_id": "001"},
        action="firewall-drop",
        rationale="brute force",
        expected_effect="drop the offending IP",
    )
    await db.commit()

    # Organization B's forced-filtered query sees nothing.
    stmt_b = select(ActionProposal).where(ActionProposal.organization_id == organization_b)
    b_rows = (await db.execute(stmt_b)).scalars().all()
    assert b_rows == []
    # Organization A sees its own proposal.
    stmt_a = select(ActionProposal).where(ActionProposal.organization_id == organization_a)
    a_rows = (await db.execute(stmt_a)).scalars().all()
    assert len(a_rows) == 1
    assert a_rows[0].organization_id == organization_a


@pytest.mark.asyncio
async def test_find_active_action_does_not_cross_organizations(db: Any) -> None:
    """Reversal recall (6-e ADR 0029) is org-scoped: a succeeded agent_action in
    org A is NOT discoverable as an active action for org B, so a reversal can
    never link across the organization boundary."""
    from wolf_server.gateway.models import ProposalState
    from wolf_server.gateway.proposals import create_proposal, find_active_action

    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()
    p = await create_proposal(
        db,
        organization_id=organization_a,
        requested_by=uuid.uuid4(),
        action_class="agent_action",
        target={"agent_id": "001"},
        action="assign_group",
        parameters={"group": "isolated"},
        rationale="quarantine",
        expected_effect="assign group",
    )
    p.state = ProposalState.succeeded
    await db.flush()

    found_a = await find_active_action(
        db, organization_id=organization_a, action_class="agent_action", matcher=lambda _p: True
    )
    assert found_a is not None and found_a.id == p.id
    found_b = await find_active_action(
        db, organization_id=organization_b, action_class="agent_action", matcher=lambda _p: True
    )
    assert found_b is None  # org B cannot see org A's action


@pytest.mark.asyncio
async def test_find_active_rule_tuning_does_not_cross_organizations(db: Any) -> None:
    """The same org boundary holds for rule_tuning recall (6-e.3): a succeeded
    rule change in org A is invisible to org B, so a restore can never reverse
    another organization's rule change."""
    from wolf_server.gateway.models import ProposalState
    from wolf_server.gateway.proposals import create_proposal, find_active_action

    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()
    p = await create_proposal(
        db,
        organization_id=organization_a,
        requested_by=uuid.uuid4(),
        action_class="rule_tuning",
        target={"rule_id": "100001"},
        action="disable_rule",
        parameters={"level": 0},
        rationale="noisy rule",
        expected_effect="silence rule",
    )
    p.state = ProposalState.succeeded
    await db.flush()

    found_a = await find_active_action(
        db, organization_id=organization_a, action_class="rule_tuning", matcher=lambda _p: True
    )
    assert found_a is not None and found_a.id == p.id
    found_b = await find_active_action(
        db, organization_id=organization_b, action_class="rule_tuning", matcher=lambda _p: True
    )
    assert found_b is None  # org B cannot see org A's rule change


@pytest.mark.asyncio
async def test_find_active_config_change_does_not_cross_organizations(db: Any) -> None:
    """The same org boundary holds for config_change recall (6-e.4): a succeeded
    ossec.conf change in org A is invisible to org B, so a restore can never
    reverse another organization's configuration change."""
    from wolf_server.gateway.models import ProposalState
    from wolf_server.gateway.proposals import create_proposal, find_active_action

    organization_a = uuid.uuid4()
    organization_b = uuid.uuid4()
    p = await create_proposal(
        db,
        organization_id=organization_a,
        requested_by=uuid.uuid4(),
        action_class="config_change",
        target={"section": "sca"},
        action="update_section",
        parameters={"section_content": "<sca><enabled>no</enabled></sca>"},
        rationale="noisy sca",
        expected_effect="disable sca",
    )
    p.state = ProposalState.succeeded
    await db.flush()

    found_a = await find_active_action(
        db, organization_id=organization_a, action_class="config_change", matcher=lambda _p: True
    )
    assert found_a is not None and found_a.id == p.id
    found_b = await find_active_action(
        db, organization_id=organization_b, action_class="config_change", matcher=lambda _p: True
    )
    assert found_b is None  # org B cannot see org A's config change
