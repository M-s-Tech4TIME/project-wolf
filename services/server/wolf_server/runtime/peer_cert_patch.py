"""Surface the TLS peer certificate into ASGI scope at request time.

The mTLS middleware (Phase 5.6-c) needs the client cert's Subject CN
to enforce the allowed-caller list, but uvicorn 0.47 does not expose
peer-cert info to ASGI by default. The transport has it
(``transport.get_extra_info("ssl_object").getpeercert()``) but
uvicorn's ``RequestResponseCycle`` builds the scope dict from a
fixed set of keys that doesn't include cert info.

This module monkey-patches uvicorn's ``RequestResponseCycle.__init__``
in both HTTP backends (h11 + httptools) to read the peer cert once
per request and stash it under ``scope["state"]["wolf_peer_cert"]``.
The patch is a no-op when there is no SSL context (plain HTTP), so
the dev no-certs path is unaffected.

If a future uvicorn release exposes peer cert info natively via an
ASGI extension (e.g. ``scope["extensions"]["tls"]``), this patch
becomes redundant and the middleware can read from there instead —
nothing else in Wolf depends on the patched location.
"""

from __future__ import annotations

import importlib
from typing import Any

_PATCHED: bool = False


def _make_patched_init(original_init: Any) -> Any:  # noqa: ANN401
    """Wrap a RequestResponseCycle.__init__ to inject peer cert into scope.

    Closes over the original ``__init__`` so the patch composes cleanly
    even if it's applied twice (idempotent via the module-level
    ``_PATCHED`` guard, but defensive layering is cheap).
    """

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        original_init(self, *args, **kwargs)
        transport = getattr(self, "transport", None)
        if transport is None:
            return
        try:
            ssl_obj = transport.get_extra_info("ssl_object")
        except Exception:  # pragma: no cover  defensive
            return
        if ssl_obj is None:
            return
        try:
            peer_cert = ssl_obj.getpeercert()
        except Exception:  # pragma: no cover  defensive
            return
        if not peer_cert:
            return
        scope = getattr(self, "scope", None)
        if scope is None or not isinstance(scope, dict):
            return
        scope.setdefault("state", {})["wolf_peer_cert"] = peer_cert

    return patched_init


def patch_uvicorn_for_peer_cert() -> None:
    """Apply the monkey-patch. Idempotent — safe to call multiple times."""
    global _PATCHED
    if _PATCHED:
        return
    for modname in (
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
    ):
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            # httptools is optional — h11 is the always-available fallback.
            continue
        cycle_cls = getattr(mod, "RequestResponseCycle", None)
        if cycle_cls is None:  # pragma: no cover  defensive
            continue
        cycle_cls.__init__ = _make_patched_init(cycle_cls.__init__)
    _PATCHED = True
