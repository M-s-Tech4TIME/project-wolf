"""Tests for Phase 6.6-e — runtime Wazuh connection resolution.

``get_wazuh_connection`` combines the install ecosystem topology (URLs + TLS,
read fresh per query, random indexer node for distributed) with the per-org
credential config (credential keys + index filter + organization-filter flag).
The per-org row's legacy URL columns are NOT read — proven here by seeding them
with stale values the resolver must ignore.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_server.organization.context import OrganizationContext
from wolf_server.organization.models import Organization
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.models import OrganizationWazuhConfig, WazuhEcosystemTopology
from wolf_server.wazuh.resolver import (
    WazuhConfigMissingError,
    WazuhTopologyMissingError,
    get_wazuh_connection,
    opensearch_credential_key,
    server_api_credential_key,
)


@pytest_asyncio.fixture(autouse=True)
async def _clean_topology(db: AsyncSession) -> None:
    """Singleton topology is shared across the suite — reset before each test."""
    await db.execute(delete(WazuhEcosystemTopology))
    await db.commit()


async def _seed_org_with_creds(
    db: AsyncSession, *, index_pattern: str = "wazuh-alerts-*"
) -> OrganizationContext:
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    org = Organization(
        id=uuid.uuid4(), name=f"Resolver {suffix}", slug=f"resolver-{suffix}",
        is_active=True, created_at=now, updated_at=now,
    )
    db.add(org)
    await db.flush()
    os_key = opensearch_credential_key(org.id)
    api_key = server_api_credential_key(org.id)
    db.add(
        OrganizationWazuhConfig(
            id=uuid.uuid4(),
            organization_id=org.id,
            # Legacy URL columns seeded STALE on purpose — the resolver must
            # ignore them and read the install topology instead.
            opensearch_url="https://stale-idx:9200",
            opensearch_index_pattern=index_pattern,
            opensearch_credential_key=os_key,
            server_api_url="https://stale-mgr:55000",
            server_api_credential_key=api_key,
            verify_tls=True,  # vestigial — topology's verify_tls wins
            inject_organization_filter=False,
            validated_at=None,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    secrets = get_secrets_backend()
    await secrets.set(os_key, json.dumps({"username": "idx-user", "password": "idx-pw"}))
    await secrets.set(api_key, json.dumps({"username": "api-user", "password": "api-pw"}))
    return OrganizationContext(
        organization_id=org.id, organization_slug=org.slug, user_id=uuid.uuid4(),
        user_email="analyst@test.example", role="analyst", session_id="sess",
    )


def _single_topology(*, indexer: str, manager: str, verify_tls: bool) -> WazuhEcosystemTopology:
    now = datetime.now(UTC)
    return WazuhEcosystemTopology(
        id=uuid.uuid4(), is_singleton=True, kind="single",
        topology={
            "kind": "single", "indexer_url": indexer,
            "manager_url": manager, "dashboard_url": "https://dash",
        },
        indexer_credential_key="wazuh.topology.indexer_admin",
        manager_credential_key="wazuh.topology.manager_api",
        verify_tls=verify_tls, validated_at=now, created_at=now, updated_at=now,
    )


async def test_resolver_uses_topology_urls_not_stale_per_org(db: AsyncSession) -> None:
    ctx = await _seed_org_with_creds(db, index_pattern="wazuh-alerts-acme-*")
    db.add(_single_topology(
        indexer="https://real-idx:9200", manager="https://real-mgr:55000", verify_tls=False,
    ))
    await db.commit()

    conn = await get_wazuh_connection(ctx, db, get_secrets_backend())
    # URLs + TLS come from the install topology, NOT the stale per-org columns.
    assert conn.opensearch_url == "https://real-idx:9200"
    assert conn.server_api_url == "https://real-mgr:55000"
    assert conn.verify_tls is False
    # Credentials + index filter + org-filter flag come from the per-org config.
    assert conn.opensearch_index_pattern == "wazuh-alerts-acme-*"
    assert conn.opensearch_username == "idx-user"
    assert conn.opensearch_password == "idx-pw"
    assert conn.server_api_username == "api-user"
    assert conn.server_api_password == "api-pw"
    assert conn.inject_organization_filter is False
    assert conn.organization_id == ctx.organization_id


async def test_resolver_distributed_picks_an_indexer_node(db: AsyncSession) -> None:
    ctx = await _seed_org_with_creds(db)
    node_urls = {"https://idx1:9200", "https://idx2:9200", "https://idx3:9200"}
    now = datetime.now(UTC)
    db.add(
        WazuhEcosystemTopology(
            id=uuid.uuid4(), is_singleton=True, kind="distributed",
            topology={
                "kind": "distributed",
                "indexer_nodes": [{"url": u} for u in sorted(node_urls)],
                "manager_master": {"url": "https://master:55000", "name": "master"},
                "manager_workers": [],
                "dashboards": [{"url": "https://dash"}],
            },
            indexer_credential_key="k1", manager_credential_key="k2",
            verify_tls=False, validated_at=now, created_at=now, updated_at=now,
        )
    )
    await db.commit()

    # Random per-query selection: every pick is a real node; master is fixed.
    for _ in range(12):
        conn = await get_wazuh_connection(ctx, db, get_secrets_backend())
        assert conn.opensearch_url in node_urls
        assert conn.server_api_url == "https://master:55000"


async def test_resolver_raises_without_topology(db: AsyncSession) -> None:
    ctx = await _seed_org_with_creds(db)  # per-org config exists, no topology
    with pytest.raises(WazuhTopologyMissingError):
        await get_wazuh_connection(ctx, db, get_secrets_backend())


async def test_resolver_raises_without_org_config(db: AsyncSession) -> None:
    db.add(_single_topology(
        indexer="https://idx:9200", manager="https://mgr:55000", verify_tls=False,
    ))
    await db.commit()
    ghost = OrganizationContext(
        organization_id=uuid.uuid4(), organization_slug="ghost", user_id=uuid.uuid4(),
        user_email="x@y.z", role="analyst", session_id="sess",
    )
    with pytest.raises(WazuhConfigMissingError):
        await get_wazuh_connection(ghost, db, get_secrets_backend())
