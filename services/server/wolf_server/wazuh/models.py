"""SQLAlchemy model for per-organization Wazuh connection configuration.

The connection URLs live here; the credentials live in the secrets backend
keyed by organization_id.  See doc 05 (Per-organization secrets) and doc 07 (Secrets
management) for the rationale.

A row in this table is treated as **immutable after validation** — changes go
through an audited admin path.  This is the "immutable connection profiles
after validation" guarantee from doc 05 §Organization misconfiguration.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from wolf_server.database import Base

# JSONB on Postgres (binary, queryable), generic JSON on SQLite (the test
# suite — SQLite has no JSONB).  Same construction as audit/models.py.
_JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class OrganizationWazuhConfig(Base):
    """One Wazuh connection profile per organization.

    The `opensearch_credential_key` and `server_api_credential_key` fields name
    the keys in the secrets backend.  Credentials themselves are never stored
    in the database.
    """

    __tablename__ = "organization_wazuh_configs"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_organization_wazuh_config_organization"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # OpenSearch (Wazuh Indexer) — alerts and raw events live here.  Phase
    # 6.6-g dropped the per-org `opensearch_url`/`server_api_url`/`verify_tls`
    # columns: since 6.6-e the runtime resolver reads URLs + TLS posture from
    # the install-level Wazuh ecosystem TOPOLOGY (a single source of truth),
    # not per-org.  Only the credential keys + index pattern + scoping live here.
    opensearch_index_pattern: Mapped[str] = mapped_column(
        String(200), nullable=False, default="wazuh-alerts-*"
    )
    opensearch_credential_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # Wazuh Server API — fleet inventory, rule definitions, cluster health.
    server_api_credential_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # Whether to inject a `terms: {agent.labels.group: [<labels>]}` clause into
    # every OpenSearch query (Phase 6.6-f, ADR 0020).  Default FALSE because the
    # per-org *credential* (its Wazuh RBAC + index DLS) is already the isolation
    # boundary — Wolf should not impose a static filter on top.  Set TRUE only
    # when the org's indexer credential is NOT itself DLS-scoped and you want
    # Wolf to scope it by the real Wazuh `agent.labels.group` field instead.
    # Requires a non-empty `agent_group_labels`.
    inject_group_label_filter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # OPTIONAL list of Wazuh `agent.labels.group` values this org is scoped to
    # (Phase 6.6-f).  When `inject_group_label_filter` is TRUE these are
    # OR-combined into the forced `terms` filter; an org's agents can belong to
    # more than one label, hence a list.  Null/empty + filter ON is rejected at
    # the API.  Surfaced in the credentials UI; not itself an authority when the
    # filter is OFF.
    agent_group_labels: Mapped[list[str] | None] = mapped_column(_JSON_TYPE, nullable=True)

    # Provisioning validation timestamp — null until the platform has connected
    # with the credentials and confirmed the deployment identity (doc 05).
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    def __repr__(self) -> str:
        validated = self.validated_at is not None
        return (
            f"<OrganizationWazuhConfig organization={self.organization_id} validated={validated}>"
        )


class WazuhEcosystemTopology(Base):
    """Install-level Wazuh ecosystem topology — Phase 6.6-a, ADR 0020.

    A SINGLE install-wide row describing where the Wazuh indexer(s),
    manager(s) and dashboard physically live (ADR 0020 decision 4: one
    install = one Wazuh ecosystem).  Distinct from
    :class:`OrganizationWazuhConfig`, which holds the per-org *credentials*
    used to query that ecosystem.

    Singleton invariant: enforced at the DB by a unique constraint on the
    constant ``is_singleton`` flag — at most one row can ever exist.

    The structural shape (URLs + cluster membership) lives in the
    ``topology`` JSON document, validated by the ``wazuh.topology`` pydantic
    discriminated union on the way in.  The install-level credentials
    (indexer admin, manager API) never appear in this row — only the secrets
    backend keys that name them (ADR 0020 decision 7).
    """

    __tablename__ = "wazuh_ecosystem_topology"
    __table_args__ = (
        UniqueConstraint("is_singleton", name="uq_wazuh_ecosystem_topology_singleton"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)

    # Always True; the unique constraint above turns it into a single-row guard.
    is_singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # "single" | "distributed" — the discriminator of the topology document.
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    # The validated structural document (see wazuh/topology.py).  URLs +
    # cluster membership only — never credentials.
    topology: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)

    # Names of the secrets-backend entries holding the install-level
    # credentials.  The passwords themselves are never stored here.
    indexer_credential_key: Mapped[str] = mapped_column(String(200), nullable=False)
    manager_credential_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # TLS verification for probes + (later, 6.6-e) runtime queries.
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Set when the last save probed every required endpoint successfully.
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    def __repr__(self) -> str:
        validated = self.validated_at is not None
        return f"<WazuhEcosystemTopology kind={self.kind} validated={validated}>"
