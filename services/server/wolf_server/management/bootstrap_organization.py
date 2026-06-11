"""Bootstrap a organization: create organization + admin user + Wazuh connection profile.

Per doc 05 §Organization misconfiguration / onboarding and Phase 4 Slice 2:
connection profiles are validated against the live Wazuh endpoints BEFORE
being persisted, and treated as immutable after first validation. A
re-run for an existing organization slug refuses unless `--update` is passed,
making accidental overwrites of a validated organization impossible.

Usage (first-time bootstrap):
  uv run python -m wolf_server.management.bootstrap_organization \\
    --organization-slug acme --organization-name "Acme Corp" \\
    --admin-email admin@acme.example --admin-password '<...>' \\
    --opensearch-url https://wazuh.example:9200 \\
    --opensearch-username wolf_ro --opensearch-password '<...>' \\
    --server-api-url https://wazuh.example:55000 \\
    --server-api-username wolf_ro --server-api-password '<...>' \\
    --no-verify-tls

To re-validate / rotate credentials for an existing organization, pass `--update`:
  uv run python -m wolf_server.management.bootstrap_organization \\
    --organization-slug acme --update ... <same args + new credentials>

For the "no Wazuh yet" placeholder pattern (cf. ONBOARDING §3.9), pass
`--skip-validation`. The organization is provisioned with `validated_at=NULL`
and a warning is printed; the agent loop will work for non-tool paths,
but any tool call hitting Wazuh fails at request time.

The OpenSearch and Server API credentials are stored in the configured
secrets backend.  They are NEVER persisted to the database.
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import models so SQLAlchemy metadata is fully populated.
import wolf_server.audit.models  # noqa: F401
import wolf_server.organization.models  # noqa: F401
import wolf_server.wazuh.models  # noqa: F401
from wolf_server.auth.local import hash_password
from wolf_server.config import get_settings
from wolf_server.database import Base
from wolf_server.organization.context import VALID_ROLES
from wolf_server.organization.models import Organization, User, UserOrganization
from wolf_server.secrets_factory import get_secrets_backend
from wolf_server.wazuh.models import OrganizationWazuhConfig
from wolf_server.wazuh.resolver import opensearch_credential_key, server_api_credential_key

logger = structlog.get_logger(__name__)


async def _ensure_schema(database_url: str) -> None:
    """For SQLite dev DBs, create tables on the fly.  Postgres uses Alembic."""
    if "sqlite" not in database_url:
        return
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _upsert_organization(db: AsyncSession, slug: str, name: str) -> Organization:
    existing = await db.scalar(select(Organization).where(Organization.slug == slug))
    if existing is not None:
        existing.name = name
        existing.is_active = True
        existing.updated_at = datetime.now(UTC)
        return existing
    organization = Organization(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(organization)
    await db.flush()
    return organization


async def _upsert_user(db: AsyncSession, email: str, password: str, display_name: str) -> User:
    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Refresh the password if provided so the operator can rotate via re-run.
        existing.hashed_password = hash_password(password)
        existing.is_active = True
        existing.updated_at = datetime.now(UTC)
        return existing
    user = User(
        id=uuid.uuid4(),
        email=email,
        display_name=display_name,
        hashed_password=hash_password(password),
        is_active=True,
        is_superuser=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    return user


async def _upsert_binding(
    db: AsyncSession, user_id: uuid.UUID, organization_id: uuid.UUID, role: str
) -> None:
    if role not in VALID_ROLES:
        raise SystemExit(f"Invalid role {role!r}; allowed: {sorted(VALID_ROLES)}")
    existing = await db.scalar(
        select(UserOrganization).where(
            UserOrganization.user_id == user_id, UserOrganization.organization_id == organization_id
        )
    )
    if existing is not None:
        existing.role = role
        return
    db.add(
        UserOrganization(
            id=uuid.uuid4(),
            user_id=user_id,
            organization_id=organization_id,
            role=role,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()


class ConnectionValidationError(RuntimeError):
    """Raised when a Wazuh connection profile fails live validation.

    Surfaced to the operator with a clear remediation hint. Wolf does
    NOT persist a OrganizationWazuhConfig row when this is raised — doc 05
    §Organization misconfiguration mandates "validate-before-persist."
    """


async def _validate_wazuh_connection(
    *,
    opensearch_url: str,
    opensearch_username: str,
    opensearch_password: str,
    server_api_url: str,
    server_api_username: str,
    server_api_password: str,
    verify_tls: bool,
) -> None:
    """Probe both Wazuh endpoints with the supplied credentials.

    Raises ConnectionValidationError with a single-sentence message
    naming the failing endpoint. The two probes are independent — both
    must succeed before the organization is persisted (Wazuh's Indexer and
    Server API have separate user backends; provisioning a user in one
    doesn't imply the other works, per the long-standing ONBOARDING
    Wazuh-creds gotcha).

    Probe shape:
      - Indexer: HEAD `/` with basic auth. Returns 200 on success,
        401 on bad creds. We tolerate 403 (cluster-monitor permission
        not granted to read-only roles) — the credential authenticated,
        which is the property we're checking.
      - Server API: POST `/security/user/authenticate` with basic auth.
        Wazuh issues a JWT on success; 401 on bad creds.
    """
    timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(verify=verify_tls, timeout=timeout) as client:
        # Indexer probe.
        try:
            response = await client.get(
                opensearch_url.rstrip("/") + "/",
                auth=(opensearch_username, opensearch_password),
            )
        except httpx.RequestError as exc:
            raise ConnectionValidationError(
                f"Indexer at {opensearch_url} is unreachable: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code == 401:
            raise ConnectionValidationError(
                f"Indexer at {opensearch_url} rejected credentials "
                f"(HTTP 401). Verify the user exists in the OpenSearch "
                f"security plugin and the password is correct."
            )
        if response.status_code not in (200, 403):
            raise ConnectionValidationError(
                f"Indexer at {opensearch_url} returned unexpected "
                f"HTTP {response.status_code}; expected 200 or 403."
            )

        # Server API probe.
        try:
            response = await client.post(
                server_api_url.rstrip("/") + "/security/user/authenticate",
                auth=(server_api_username, server_api_password),
            )
        except httpx.RequestError as exc:
            raise ConnectionValidationError(
                f"Server API at {server_api_url} is unreachable: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code == 401:
            raise ConnectionValidationError(
                f"Server API at {server_api_url} rejected credentials "
                f"(HTTP 401). Note: the Wazuh Server API has its OWN "
                f"user database, separate from the Indexer. The user "
                f"that works for the Indexer may not exist on the "
                f"Server API (typical example: 'admin' authenticates "
                f"the Indexer but the Server API wants 'wazuh-wui')."
            )
        if response.status_code != 200:
            raise ConnectionValidationError(
                f"Server API at {server_api_url} returned unexpected "
                f"HTTP {response.status_code}; expected 200."
            )


async def _upsert_wazuh_config(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    opensearch_url: str,
    opensearch_index_pattern: str,
    opensearch_credential_key: str,  # noqa: A002 — names the column
    server_api_url: str,
    server_api_credential_key: str,  # noqa: A002
    verify_tls: bool,
    inject_organization_filter: bool,
    validated: bool,
) -> OrganizationWazuhConfig:
    now = datetime.now(UTC)
    existing = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == organization_id
        )
    )
    if existing is not None:
        existing.opensearch_url = opensearch_url
        existing.opensearch_index_pattern = opensearch_index_pattern
        existing.opensearch_credential_key = opensearch_credential_key
        existing.server_api_url = server_api_url
        existing.server_api_credential_key = server_api_credential_key
        existing.verify_tls = verify_tls
        existing.inject_organization_filter = inject_organization_filter
        existing.validated_at = now if validated else None
        existing.updated_at = now
        return existing
    cfg = OrganizationWazuhConfig(
        id=uuid.uuid4(),
        organization_id=organization_id,
        opensearch_url=opensearch_url,
        opensearch_index_pattern=opensearch_index_pattern,
        opensearch_credential_key=opensearch_credential_key,
        server_api_url=server_api_url,
        server_api_credential_key=server_api_credential_key,
        verify_tls=verify_tls,
        inject_organization_filter=inject_organization_filter,
        validated_at=now if validated else None,
        created_at=now,
        updated_at=now,
    )
    db.add(cfg)
    await db.flush()
    return cfg


class OrganizationAlreadyExistsError(RuntimeError):
    """Raised on a re-bootstrap attempt without `--update`.

    Doc 05 §Organization misconfiguration says connection profiles are
    immutable by default after validation; this exception enforces it
    at the CLI boundary. Operator can explicitly opt-in to overwrite
    by passing the `--update` flag.
    """


async def _organization_exists_with_validated_config(
    db: AsyncSession, organization_slug: str
) -> tuple[bool, bool]:
    """Returns (organization_exists, has_validated_wazuh_config).

    Used by the bootstrap path to decide between first-time provisioning
    (proceed), re-bootstrap without --update (refuse), and re-bootstrap
    with --update (proceed with re-validation).
    """
    organization = await db.scalar(
        select(Organization).where(Organization.slug == organization_slug)
    )
    if organization is None:
        return False, False
    cfg = await db.scalar(
        select(OrganizationWazuhConfig).where(
            OrganizationWazuhConfig.organization_id == organization.id
        )
    )
    return True, (cfg is not None and cfg.validated_at is not None)


async def bootstrap_organization(
    *,
    organization_slug: str,
    organization_name: str,
    admin_email: str,
    admin_password: str,
    admin_display_name: str,
    role: str,
    opensearch_url: str,
    opensearch_username: str,
    opensearch_password: str,
    opensearch_index_pattern: str,
    server_api_url: str,
    server_api_username: str,
    server_api_password: str,
    verify_tls: bool,
    inject_organization_filter: bool,
    update: bool = False,
    skip_validation: bool = False,
) -> dict[str, Any]:
    """Bootstrap or update a organization.

    First-time call: validates the Wazuh connection (or skips via
    `skip_validation=True`), then provisions the organization + admin + Wazuh
    config + secrets.

    Re-run for an existing organization slug: refuses with
    OrganizationAlreadyExistsError unless `update=True`. With update=True,
    re-validates and overwrites the existing config + credentials. The
    organization + admin-user rows are kept (preserves user↔organization bindings)
    but the Wazuh config row is updated in place and a new
    `validated_at` is stamped on success.
    """
    settings = get_settings()
    await _ensure_schema(settings.database_url)

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # First check: is this a re-run on an existing organization?
    async with factory() as db:
        exists, has_validated_config = await _organization_exists_with_validated_config(
            db, organization_slug
        )
    if exists and has_validated_config and not update:
        await engine.dispose()
        raise OrganizationAlreadyExistsError(
            f"Organization slug={organization_slug!r} already exists with a validated "
            f"Wazuh config (per doc 05 §Organization misconfiguration, validated "
            f"profiles are immutable by default). Pass `--update` to "
            f"re-validate and overwrite the configuration + credentials. "
            f"Run with --skip-validation only if you understand the agent "
            f"loop will fail on any tool call that hits Wazuh."
        )

    # Validate (or explicitly skip) BEFORE writing anything to the DB.
    validated = False
    if not skip_validation:
        await _validate_wazuh_connection(
            opensearch_url=opensearch_url,
            opensearch_username=opensearch_username,
            opensearch_password=opensearch_password,
            server_api_url=server_api_url,
            server_api_username=server_api_username,
            server_api_password=server_api_password,
            verify_tls=verify_tls,
        )
        validated = True

    async with factory() as db:
        organization = await _upsert_organization(db, organization_slug, organization_name)
        user = await _upsert_user(db, admin_email, admin_password, admin_display_name)
        await _upsert_binding(db, user.id, organization.id, role)

        os_key = opensearch_credential_key(organization.id)
        api_key = server_api_credential_key(organization.id)
        await _upsert_wazuh_config(
            db,
            organization.id,
            opensearch_url=opensearch_url,
            opensearch_index_pattern=opensearch_index_pattern,
            opensearch_credential_key=os_key,
            server_api_url=server_api_url,
            server_api_credential_key=api_key,
            verify_tls=verify_tls,
            inject_organization_filter=inject_organization_filter,
            validated=validated,
        )
        await db.commit()
        organization_id = organization.id
        user_id = user.id

    secrets = get_secrets_backend(settings)
    await secrets.set(
        opensearch_credential_key(organization_id),
        json.dumps({"username": opensearch_username, "password": opensearch_password}),
    )
    await secrets.set(
        server_api_credential_key(organization_id),
        json.dumps({"username": server_api_username, "password": server_api_password}),
    )

    await engine.dispose()

    return {
        "organization_id": str(organization_id),
        "organization_slug": organization_slug,
        "user_id": str(user_id),
        "user_email": admin_email,
        "verify_tls": verify_tls,
        "inject_organization_filter": inject_organization_filter,
        "validated": validated,
        "mode": "update" if (exists and has_validated_config) else "create",
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--organization-slug", required=True)
    p.add_argument("--organization-name", required=True)
    p.add_argument("--admin-email", required=True)
    p.add_argument("--admin-password", required=True)
    p.add_argument("--admin-display-name", default="Organization Admin")
    p.add_argument("--role", default="admin", choices=sorted(VALID_ROLES))
    p.add_argument("--opensearch-url", required=True)
    p.add_argument("--opensearch-index-pattern", default="wazuh-alerts-*")
    p.add_argument("--opensearch-username", required=True)
    p.add_argument("--opensearch-password", required=True)
    p.add_argument("--server-api-url", required=True)
    p.add_argument("--server-api-username", required=True)
    p.add_argument("--server-api-password", required=True)
    tls = p.add_mutually_exclusive_group()
    tls.add_argument(
        "--verify-tls",
        dest="verify_tls",
        action="store_true",
        help="Validate TLS certificates (default).",
    )
    tls.add_argument(
        "--no-verify-tls",
        dest="verify_tls",
        action="store_false",
        help="Skip TLS validation (self-signed certs).",
    )
    p.set_defaults(verify_tls=True)

    tf = p.add_mutually_exclusive_group()
    tf.add_argument(
        "--inject-organization-filter",
        dest="inject_organization_filter",
        action="store_true",
        help=(
            "Inject `term:{organization_id:<id>}` into every OpenSearch query. "
            "Use only for pooled-index multi-organization Wazuh setups where every "
            "alert is stamped with organization_id at ingest."
        ),
    )
    tf.add_argument(
        "--no-inject-organization-filter",
        dest="inject_organization_filter",
        action="store_false",
        help=(
            "Do NOT inject the organization_id filter (default). For "
            "separate-deployment-per-organization the credential is the "
            "isolation boundary; filtering on a missing field would "
            "silently return zero hits."
        ),
    )
    p.set_defaults(inject_organization_filter=False)

    # Phase 4 Slice 2 — re-bootstrap and skip-validation flags.
    p.add_argument(
        "--update",
        action="store_true",
        help=(
            "Re-validate and overwrite an existing organization's Wazuh config + "
            "credentials. Required when re-running this CLI for an already-"
            "validated organization slug; doc 05 §Organization misconfiguration treats "
            "validated profiles as immutable by default."
        ),
    )
    p.add_argument(
        "--skip-validation",
        action="store_true",
        help=(
            "Skip the live Wazuh connection probe. Use for the 'no Wazuh "
            "yet' placeholder pattern in ONBOARDING §3.9 — the organization is "
            "provisioned with validated_at=NULL and any tool call hitting "
            "Wazuh will fail at request time."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        result = asyncio.run(
            bootstrap_organization(
                organization_slug=args.organization_slug,
                organization_name=args.organization_name,
                admin_email=args.admin_email,
                admin_password=args.admin_password,
                admin_display_name=args.admin_display_name,
                role=args.role,
                opensearch_url=args.opensearch_url,
                opensearch_index_pattern=args.opensearch_index_pattern,
                opensearch_username=args.opensearch_username,
                opensearch_password=args.opensearch_password,
                server_api_url=args.server_api_url,
                server_api_username=args.server_api_username,
                server_api_password=args.server_api_password,
                verify_tls=args.verify_tls,
                inject_organization_filter=args.inject_organization_filter,
                update=args.update,
                skip_validation=args.skip_validation,
            )
        )
    except OrganizationAlreadyExistsError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 4
    except ConnectionValidationError as exc:
        sys.stderr.write(
            f"ERROR: Wazuh connection validation failed:\n  {exc}\n"
            f"  Re-run with --skip-validation to provision anyway (the "
            f"organization will have validated_at=NULL and tool calls hitting "
            f"Wazuh will fail at request time).\n"
        )
        return 5
    if args.skip_validation:
        sys.stderr.write(
            "WARNING: Wazuh connection NOT validated (--skip-validation). "
            "Re-run without --skip-validation once Wazuh is reachable to "
            "mark the organization validated.\n"
        )
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
