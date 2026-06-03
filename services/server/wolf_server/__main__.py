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

The historic `uvicorn wolf_server.main:app --host 0.0.0.0 --port 8000`
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

from dataclasses import dataclass
from pathlib import Path

import uvicorn

from wolf_server.config import get_settings


@dataclass(frozen=True)
class TlsResolution:
    """Pure-data result of `resolve_tls()` — what the launcher will
    actually pass to uvicorn. `cert_path` and `key_path` are non-None
    iff TLS is on; the reason string is for the startup log so the
    operator sees WHY the orchestrator picked the scheme it did."""

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

    scheme = "https" if tls.use_https else "http"
    print(
        f"wolf-server: serving {scheme}://"
        f"{settings.bind_host}:{settings.bind_port}",
    )
    print(f"  reason: {tls.reason}")

    uvicorn.run(
        "wolf_server.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        ssl_certfile=str(tls.cert_path) if tls.cert_path else None,
        ssl_keyfile=str(tls.key_path) if tls.key_path else None,
    )


if __name__ == "__main__":  # pragma: no cover — trivial dispatch
    main()
