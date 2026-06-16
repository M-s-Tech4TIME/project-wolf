"""wolf-server launcher — Phase 5.4-c (renamed in Phase 5.5).

Wraps `uvicorn` so wolf-server listens HTTPS when its TLS cert
files exist and falls back to plain HTTP otherwise. The cert files
themselves are the signal: `wolf-cert init` mints them under
`.local/certs/server/`, the next `python -m wolf_server` start
picks them up, and wolf-server flips to HTTPS with no env edits.
`wolf-cert revoke` deletes them; the launcher drops back to HTTP.

Two invocation forms supported:

    python -m wolf_server          # what docs/restart.md now recommends
    uv run python -m wolf_server   # same, via uv's venv resolution

The historic `uvicorn wolf_server.main:app --host 0.0.0.0 --port 7860`
incantation still works for cases where uvicorn flags need
hand-tuning — it just bypasses the auto-HTTPS detection.

Design choices
--------------
* **Path-existence is the contract.** Not an env flag, not a config
  enum. Operators with a custom layout override
  `TLS_CERT_PATH` / `TLS_KEY_PATH`; everyone else just runs
  `wolf-cert init` and gets HTTPS on the next start. This keeps
  the dev → prod path identical.
* **Both files must exist.** A cert without its key (or vice-versa)
  is a broken state; we don't try to limp along on HTTPS-with-one-
  file. Fall back to HTTP and log a warning so the operator can
  decide.
* **Decision logic is a pure function** (`resolve_tls()`) so tests
  can exercise it without launching uvicorn. The launcher itself
  is a thin glue layer.

CORS regex (`cors_allow_origin_regex` in config) already matches
both `http://` and `https://` schemes, so flipping wolf-server to
HTTPS doesn't require a CORS edit.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from wolf_server.config import get_settings
from wolf_server.runtime.peer_cert_patch import patch_uvicorn_for_peer_cert


@dataclass(frozen=True)
class TlsResolution:
    """Pure-data result of `resolve_tls()` — what the launcher will
    actually pass to uvicorn. `cert_path` and `key_path` are non-None
    iff TLS is on; the reason string is for the startup log so the
    operator sees WHY wolf-server picked the scheme it did."""

    cert_path: Path | None
    key_path: Path | None
    reason: str

    @property
    def use_https(self) -> bool:
        return self.cert_path is not None and self.key_path is not None


def resolve_tls(cert_path: str, key_path: str) -> TlsResolution:
    """Decide whether to start in HTTPS or HTTP mode based purely on
    filesystem state of the configured cert and key paths.

    Truth table (cert file, key file → outcome):
      (present,  present)  → HTTPS, both paths returned
      (present,  missing)  → HTTP, broken-pair warning in `reason`
      (missing,  present)  → HTTP, broken-pair warning in `reason`
      (missing,  missing)  → HTTP, "no certs" reason
    """
    cert = Path(cert_path)
    key = Path(key_path)
    cert_ok = cert.is_file()
    key_ok = key.is_file()

    if cert_ok and key_ok:
        return TlsResolution(
            cert_path=cert,
            key_path=key,
            reason=f"TLS cert+key present at {cert} and {key}",
        )
    if cert_ok ^ key_ok:
        # Exactly one of the two is missing — broken pair. We don't
        # try to half-start HTTPS; that just produces obscure TLS
        # handshake failures later. Surface the inconsistency loudly
        # and fall back to HTTP so the operator can fix it.
        missing = key if cert_ok else cert
        return TlsResolution(
            cert_path=None,
            key_path=None,
            reason=(
                f"TLS pair incomplete — {missing} is missing; falling back "
                "to HTTP. Run `wolf-cert renew` (or `wolf-cert init`) to "
                "regenerate."
            ),
        )
    return TlsResolution(
        cert_path=None,
        key_path=None,
        reason=(
            f"No TLS cert at {cert} — starting on HTTP. Run `wolf-cert "
            "init` to mint a self-signed pair and the next start will "
            "auto-upgrade."
        ),
    )


def main() -> None:
    settings = get_settings()
    tls = resolve_tls(settings.tls_cert_path, settings.tls_key_path)

    # Phase 5.6-c: mTLS auto-detect. When the Wolf CA + server leaf
    # all exist, switch uvicorn's TLS layer to client-cert mode
    # (CERT_OPTIONAL — required-ness is enforced by the ASGI
    # MtlsMiddleware so we can return useful 401s + bypass /healthz
    # from loopback). The peer-cert patch surfaces the verified cert
    # into ASGI scope so the middleware can read its Subject CN.
    mtls_on = tls.use_https and settings.mtls_enabled
    if mtls_on:
        patch_uvicorn_for_peer_cert()

    # Banner: three lines, one per mode dimension, so the operator
    # can grep the first line for the scheme and the next for the
    # full security posture without scanning prose. Phase 5.6-d:
    # the mTLS line is always present (either ENABLED or DISABLED)
    # so the absence of the keyword in the log is itself diagnostic.
    scheme = "https" if tls.use_https else "http"
    print(
        f"wolf-server: serving {scheme}://{settings.bind_host}:{settings.bind_port}",
    )
    print(f"  TLS:  {tls.reason}")
    if mtls_on:
        cns = ", ".join(settings.mtls_allowed_client_cn_list)
        print(
            f"  mTLS: ENABLED — Wolf CA at {settings.mtls_ca_path}; allowed client CNs: [{cns}]",
        )
    else:
        print(
            "  mTLS: DISABLED — no Wolf CA cert at "
            f"{settings.mtls_ca_path} (run `wolf-cert init` to enable)",
        )

    # Phase 6.5-h.2: the same-network verification gate. Always print one
    # line (ENABLED or DISABLED) so the absence of the keyword is itself
    # diagnostic, mirroring the mTLS banner above.
    if settings.same_network_gate_enabled:
        print(
            "  same-network gate: ENABLED — invite verification only from "
            "Wolf's own NIC CIDRs (set SAME_NETWORK_GATE_ENABLED=0 to disable "
            "for remote-admin deploys)",
        )
    else:
        print(
            "  same-network gate: DISABLED — invite verification allowed from "
            "any network (set SAME_NETWORK_GATE_ENABLED=1 to enforce)",
        )

    uvicorn.run(
        "wolf_server.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        ssl_certfile=str(tls.cert_path) if tls.cert_path else None,
        ssl_keyfile=str(tls.key_path) if tls.key_path else None,
        ssl_ca_certs=settings.mtls_ca_path if mtls_on else None,
        ssl_cert_reqs=ssl.CERT_OPTIONAL if mtls_on else ssl.CERT_NONE,
    )


if __name__ == "__main__":  # pragma: no cover — trivial dispatch
    main()
