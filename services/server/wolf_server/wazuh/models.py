"""SQLAlchemy model for per-tenant Wazuh connection configuration.

The connection URLs live here; the credentials live in the secrets backend
keyed by tenant_id.  See doc 05 (Per-tenant secrets) and doc 07 (Secrets
management) for the rationale.

A row in this table is treated as **immutable after validation** — changes go
through an audited admin path.  This is the "immutable connection profiles
after validation" guarantee from doc 05 §Tenant misconfiguration.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from wolf_server.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TenantWazuhConfig(Base):
    """One Wazuh connection profile per tenant.

    The `opensearch_credential_key` and `server_api_credential_key` fields name
    the keys in the secrets backend.  Credentials themselves are never stored
    in the database.
    """

    __tablename__ = "tenant_wazuh_configs"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_wazuh_config_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # OpenSearch (Wazuh Indexer) — alerts and raw events live here.
    opensearch_url: Mapped[str] = mapped_column(String(500), nullable=False)
    opensearch_index_pattern: Mapped[str] = mapped_column(
        String(200), nullable=False, default="wazuh-alerts-*"
    )
    opensearch_credential_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # Wazuh Server API — fleet inventory, rule definitions, cluster health.
    server_api_url: Mapped[str] = mapped_column(String(500), nullable=False)
    server_api_credential_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # TLS verification — defaults to True; an explicit operator override is
    # required to disable for self-signed certs (doc 07 §Transport security).
    verify_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Whether to inject a `term: {tenant_id: <id>}` clause into every
    # OpenSearch query.  Default FALSE because vanilla Wazuh alert
    # documents do not carry a `tenant_id` field — the filter would
    # silently match zero docs.  Set TRUE only for pooled-index multi-
    # tenant deployments where you have stamped `tenant_id` onto every
    # alert at ingest time.  For separate-deployment-per-tenant (the
    # common case), the credential alone provides isolation.
    inject_tenant_filter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Provisioning validation timestamp — null until the platform has connected
    # with the credentials and confirmed the deployment identity (doc 05).
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    def __repr__(self) -> str:
        validated = self.validated_at is not None
        return f"<TenantWazuhConfig tenant={self.tenant_id} validated={validated}>"
