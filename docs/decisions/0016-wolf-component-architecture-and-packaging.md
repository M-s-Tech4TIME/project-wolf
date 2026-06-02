# 0016 — Wolf component architecture & packaging

**Date:** 2026-06-03
**Status:** accepted
**Decider:** human (project owner) with claude-code drafting
**Related:** [`native-https-and-wolf-cert.md`](../../README.md) (cross-session memory),
[`wolf-knowledge-relay.md`](../../README.md) (cross-session memory),
ADR 0007 (native non-container delivery — `.deb` / `.rpm` + systemd),
ADR 0008 (native delivery primary, Docker baseline-supported),
Phase 5.4 commits (`9a44b65` → `b064b82`) which established the
shared-CA substrate this architecture builds on.

## Context

Phase 5.4 (Native HTTPS + `wolf-cert` CLI) shipped a self-signed
CA + leaf-cert lifecycle that flips both the orchestrator and the
frontend dev server to TLS when cert files exist on disk. The
orchestrator and frontend serve two **separate** browser-visible
HTTPS origins (`:3000` and `:8000` today). The end-to-end
verification surfaced a real UX failure mode: after the user
clicks through the "not secure" warning for the frontend, the
frontend's JS does a cross-origin `fetch()` to the orchestrator,
which the browser silently blocks because the orchestrator's cert
is signed by a CA the browser doesn't trust yet — producing the
opaque `Runtime TypeError — NetworkError when attempting to fetch
resource`.

The operator (project owner) considered two paths to fix this:

1. **Trust-portal UX** — a setup wizard in the frontend that
   detects the cross-origin fetch failure, surfaces a "trust the
   Wolf root CA on this machine" page, links the operator to the
   CA download (`wolf-cert export-ca`) plus per-OS install
   instructions. This was originally floated as "slice 5.4-f."
2. **Edge-component architecture** — same model as Wazuh: only
   ONE Wolf origin (the dashboard) is visible to the browser;
   the dashboard reverse-proxies all API calls to the
   orchestrator internally via mTLS using the shared Wolf CA.
   The browser only ever makes one trust decision.

The operator explicitly rejected option 1 — *"this will perhaps
lead toward a bad user experience… my goal is to solve this error
from Next.js, not by means of force installation of CA trust
file"* — and chose option 2 with an additional set of project-
wide architectural commitments:

* Wolf should be **organized like Wazuh from the start**, with
  named first-class components, systemd-managed lifecycle, FHS
  install paths, and apt/rpm packaging.
* Three deployable components, three independent systemd units:
  `wolf-dashboard`, `wolf-server`, `wolf-database`.
* Wolf must support both **all-in-one** (one host) and
  **distributed** (each component on its own host) deployments.
* Operator tooling (`wolf-cert`, future `wolf-status` /
  `wolf-backup`) lives under `bin/` in the repo and installs to
  `/usr/bin/` on target hosts.
* APT (Debian/Ubuntu) packaging is the first-priority distro
  channel; DNF (RHEL/CentOS) is the second-priority. **Both are
  deferred to the final-release phase** — they are NOT part of
  the active 5.5 → 5.8 work program.

## Decision

Wolf adopts a **three-component edge-server-database architecture
modelled on Wazuh's `dashboard ↔ manager ↔ indexer` topology**,
managed by per-component systemd units, installed under the FHS
layout, with operator CLIs shipped to `/usr/bin/`. A shared
Wolf CA (already minted by `wolf-cert init` per Phase 5.4) issues
identity leaves to every component, and inter-component traffic
is mTLS-authenticated end to end.

### 1. Component model and naming

| Component | Role | Replaces / introduces |
|---|---|---|
| **`wolf-dashboard`** | Next.js edge process. The ONLY Wolf origin the browser sees. Serves the UI and mounts `/api/v1/*` reverse-proxy routes that forward to `wolf-server` over mTLS. | Renames the current `frontend/` |
| **`wolf-server`** | FastAPI brain. Auth, agent loop, tools, audit, model dispatch. Binds `127.0.0.1` in all-in-one and `0.0.0.0` with mTLS-required in distributed. | Renames the current `services/orchestrator/` |
| **`wolf-database`** | Postgres 17 + pgvector wrapped in a Wolf-managed systemd unit (Wazuh-indexer pattern). Data dir, configs, migrations, backup tooling all under Wolf's control. | New — bundled |
| **`wolf-gateway`** | Phase 6 propose/execute service. Separate systemd unit, disabled by default pre-Phase-6. | Renames the current `services/gateway/` |

Two operator CLIs ship today: `wolf-cert` (cert lifecycle).
Future CLIs (`wolf-status`, `wolf-backup`, future `wolf-trust`)
follow the same shape — they live under `bin/` in the repo and
install to `/usr/bin/`.

### 2. Deployment topologies

Both topologies use the same packages, the same configs, the
same systemd unit files. The only thing that differs is which
units are running on each host.

**All-in-one** — one host, all four components:

```
host-A:
  wolf-dashboard.service     :3000   (or :443 in prod)
  wolf-server.service        :8000   bound 127.0.0.1
  wolf-database.service      :5432   bound 127.0.0.1
  wolf-gateway.service       (disabled until Phase 6)
```

`wolf-server` ↔ `wolf-database` is a loopback connection; TLS
is optional. `wolf-dashboard` ↔ `wolf-server` uses mTLS on the
loopback anyway (consistent posture).

**Distributed** — each component on its own host:

```
host-D (dashboard):
  wolf-dashboard.service     :443 (public-ish facing)
  cert: dashboard leaf (DUAL — serves browser AND client-auths to server)

host-S (server):
  wolf-server.service        :8000 bound 0.0.0.0 with mTLS REQUIRED
  cert: server leaf (DUAL — serves dashboard/relay AND client-auths to database)

host-DB (database):
  wolf-database.service      :5432 bound 0.0.0.0 with TLS REQUIRED
  cert: database leaf (SERVER)
```

The cross-host traffic is **all mTLS** under the shared Wolf CA.
The operator runs `wolf-cert init` once at the admin workstation
and distributes the relevant cert subset to each host (each host
gets only its own leaf cert + key + the CA cert — never the CA
private key).

### 3. Trust model

Single shared CA (`Wolf Root CA`), one leaf cert per component
instance. The leaf cert IS the component's identity for mTLS
handshakes. Mapped to the existing `wolf_cert` library:

| Trust boundary | TLS kind | Cert kinds at each end |
|---|---|---|
| Browser → `wolf-dashboard` | one-way TLS | dashboard: SERVER (with both dashboard hostnames + IPs in SAN) |
| `wolf-dashboard` → `wolf-server` | mTLS | dashboard: DUAL · server: DUAL |
| `wolf-server` → `wolf-database` | TLS (initial) / mTLS (future hardening) | server: DUAL · database: SERVER |
| (future) `wolf-relay` → `wolf-server` | mTLS | relay: CLIENT (one per tenant) · server: DUAL |
| (future) `wolf-server` → `wolf-gateway` | mTLS | server: DUAL · gateway: DUAL |

`LeafKind.SERVER`, `LeafKind.CLIENT`, and `LeafKind.DUAL` already
exist in [`packages/cert/wolf_cert/authority.py`](../../packages/cert/wolf_cert/authority.py)
(shipped 5.4-a). Phase 5.6 adds an ASGI middleware on
`wolf-server` that **rejects any TLS connection without a valid
client cert signed by the Wolf CA** — defence-in-depth on top of
the existing JWT auth.

The CA private key NEVER leaves the operator's admin workstation
in production. Each component host receives only its own leaf
private key + the CA's *public* cert (for chain verification).
`wolf-cert` already enforces 0600 on key files; the distribution
mechanism in Phase 5.8 will preserve this.

### 4. systemd lifecycle

Per-component systemd units shipping in Phase 5.8:

```
/usr/lib/systemd/system/
  wolf-database.service
  wolf-server.service
  wolf-dashboard.service
  wolf-gateway.service     (disabled by default)
```

Each unit:

* Runs as the dedicated `wolf` system user (created by postinst).
* `EnvironmentFile=` points at `/etc/wolf-<component>/wolf-<component>.conf`.
* Logs to journald (no `/var/log/wolf-*/` files — `journalctl -u wolf-<component>` is the supported tail).
* Restart policy: `on-failure` with a 5s back-off.

Dependency chain (all-in-one):
```
wolf-database.service          (no Requires)
wolf-server.service            Requires=wolf-database.service
wolf-dashboard.service         Requires=wolf-server.service
wolf-gateway.service           Requires=wolf-server.service (when enabled)
```

In distributed mode the operator simply disables / masks the
units they're not running on each host. The dependency chain
across hosts is enforced by health-check polling on the depending
component's startup (e.g., `wolf-server` waits for
`wolf-database` to accept TLS connections before announcing
ready).

### 5. FHS install layout

Full FHS — no `/opt/wolf/`. Each component has its own slice of
the filesystem hierarchy:

```
/usr/bin/                                  Operator CLIs (shipped from repo `bin/`)
  wolf-cert                                  cert lifecycle
  wolf-status         (future)               component health rundown
  wolf-backup         (future)               db + config backup
  wolf-trust          (future)               trust-store helper

/usr/lib/wolf-dashboard/                   Component code + dependencies
/usr/lib/wolf-server/                        installed read-only from
/usr/lib/wolf-database/                      package extraction
/usr/lib/wolf-gateway/

/etc/wolf-dashboard/
  wolf-dashboard.conf                      EnvironmentFile (systemd reads this)
  certs/                                   leaf cert + key + ca-cert.pem
    cert.pem    (0644 wolf:wolf)
    key.pem     (0600 wolf:wolf)
    ca-cert.pem (0644 wolf:wolf)
  conf.d/                                  drop-in dir for overrides
/etc/wolf-server/                          same shape
/etc/wolf-database/                        same shape + postgresql.conf
/etc/wolf-gateway/                         same shape

/var/lib/wolf-dashboard/                   (mostly empty — Next.js needs no state)
/var/lib/wolf-server/                      session keys, cache state if any
/var/lib/wolf-database/                    Postgres data dir (the big one)
/var/lib/wolf-gateway/                     state (Phase 6)

/var/log/                                  (nothing — journald is the log target)
```

The certs sit alongside their owning component's config — each
host only carries the certs it actually needs (Wazuh's pattern).

### 6. Operator-facing commands

After Phase 5.8 lands, the operator interacts with Wolf entirely
through `systemctl` + `/usr/bin/wolf-*` CLIs:

```bash
# Component lifecycle
systemctl start  wolf-database wolf-server wolf-dashboard
systemctl status wolf-server
systemctl restart wolf-dashboard
systemctl stop   wolf-gateway      # (Phase 6+)

# Cert lifecycle
wolf-cert init                     # mints CA + leaves for every local component
wolf-cert status                   # prints CN/SAN/validity/fingerprint per component
wolf-cert renew --years 100        # extends leaf validity
wolf-cert add-host wolf.acme.io    # adds a SAN, reissues affected leaves
wolf-cert export-ca --out /tmp/ca.pem
wolf-cert revoke --yes             # wipes the entire cert directory

# Logs
journalctl -u wolf-server -f       # tail server logs in real time
journalctl -u wolf-database --since "1 hour ago" --grep error
```

### 7. Repo-level layout

Source-tree structure after Phase 5.5 (the renaming refactor):

```
project-wolf/
├── bin/                            Shipped operator CLIs (wrappers/shims)
│   ├── wolf-cert                     wraps `python -m wolf_cert` + venv resolution
│   └── (future) wolf-status, wolf-backup, wolf-trust
├── tools/                          Dev-internal CLIs (probes, smoke tests, ad-hoc)
│   ├── tenant_isolation_test/
│   ├── seed_knowledge/
│   └── model_probe/
├── packages/                       Shared Python libraries
│   ├── common/, secrets/, schema/, cert/
├── services/                       Deployable components
│   ├── dashboard/                  was: frontend/
│   ├── server/                     was: services/orchestrator/
│   ├── database/                   new — wolf-database wrapper + migrations
│   └── gateway/                    Phase 6 stub (renamed wolf-gateway internally)
├── deploy/
│   ├── systemd/                    *.service unit files (Phase 5.8)
│   ├── etc-wolf/                   default /etc/wolf-* configs (Phase 5.8)
│   └── packaging/                  (Phase 5.9+ — DEFERRED)
│       ├── debian/                 .deb build (deferred)
│       └── rpm/                    .rpm build (deferred)
├── docs/
└── ...
```

### 8. The `NetworkError` resolution path

Concrete trace of how the user's reported `Runtime TypeError —
NetworkError when attempting to fetch resource` disappears under
this architecture:

Today (Phase 5.4 state — TWO browser-visible origins):
```
Browser
  ├── GET  https://host:3000/  → wolf-frontend serves the UI (trust #1)
  └── fetch https://host:8000/api/v1/auth/login
                              → blocked: cert signed by untrusted CA
                              → NetworkError
```

After Phase 5.6 (the architectural fix this ADR commits to):
```
Browser
  ├── GET  https://host:3000/         → wolf-dashboard serves the UI (one trust)
  └── fetch /api/v1/auth/login        → SAME-ORIGIN — relative URL
                                      → reaches wolf-dashboard's Next.js
                                        API route handler
                                          ↓ mTLS (Wolf CA)
                                        wolf-server:8000  (bound 127.0.0.1)
                                          ↓ Postgres wire
                                        wolf-database:5432
```

Single browser-visible origin. The browser's cross-origin policy
no longer applies because there's only one origin to talk to.
The user clicks through the warning ONCE for `wolf-dashboard` (or
imports the CA once for a permanent fix); everything else just
works.

### 9. Phase ordering (active program of work)

| Phase | Scope | Status |
|---|---|---|
| **5.4** | Native HTTPS + `wolf-cert` CLI | CLOSED 2026-06-03 |
| **5.5** | Component-renaming refactor (frontend → wolf-dashboard, orchestrator → wolf-server; wolf-cert leaves renamed). No functional change. | open after ADR sign-off |
| **5.6** | Edge-component architecture + mTLS. dashboard reverse-proxies to server. ASGI middleware on server rejects calls without a valid client cert. **NetworkError dies here.** | follows 5.5 |
| **5.7** | `wolf-database` extraction. Bundled Postgres + pgvector. Migrations + configs + backup tooling. Wolf-managed systemd unit wrapping `postgresql.service`. | follows 5.6 |
| **5.8** | systemd units (`wolf-dashboard.service`, `wolf-server.service`, `wolf-database.service`, `wolf-gateway.service`). `/bin/` CLI layout. FHS install paths. Operator-facing `systemctl` story works. **No `.deb` yet.** | follows 5.7 |
| **5.9** | APT packaging (.deb). | **DEFERRED** to final-release phase |
| **5.10** | DNF/YUM packaging (.rpm). | **DEFERRED** to final-release phase |

Phases 5.9 and 5.10 are deliberately held back. Until then,
operators install via the dev path (`git clone` + `uv sync`).
This lets us iterate on systemd / FHS / configs without the
overhead of rebuilding packages on every change.

### 10. The Wazuh parallel

Pointing at this for the avoidance of doubt — Wolf is
**deliberately** mirroring Wazuh's component model because the
target operator audience (Wazuh administrators / SOC engineers /
MSSPs) already runs Wazuh and understands this shape:

| Wazuh | Wolf |
|---|---|
| `wazuh-indexer` (forked OpenSearch) | `wolf-database` (Postgres + pgvector) |
| `wazuh-manager` | `wolf-server` |
| `wazuh-dashboard` | `wolf-dashboard` |
| `wazuh-agent` | (no parallel — Wolf reads Wazuh, doesn't enroll endpoints) |
| `wazuh-certs-tool.sh` | `wolf-cert` |
| `/var/ossec/` | FHS-distributed across `/usr/lib/wolf-*`, `/etc/wolf-*`, `/var/lib/wolf-*` |
| `systemctl <verb> wazuh-<component>` | `systemctl <verb> wolf-<component>` |
| `.deb` / `.rpm` packages | `.deb` (5.9) / `.rpm` (5.10) — deferred to release phase |

The CLI shape (`wolf-cert init` ↔ `wazuh-certs-tool.sh`),
component naming (`wolf-<role>` ↔ `wazuh-<role>`), systemd unit
naming (`wolf-<role>.service`), and install-path layout all
follow Wazuh's conventions. An operator who already runs Wazuh
will find Wolf operationally familiar.

## Consequences

### Positive

* **One trust decision per Wolf install, not two.** The
  `NetworkError` disappears under Phase 5.6 without forcing the
  operator into a CA-install wizard.
* **Distributed deployment becomes viable.** Each component can
  run on its own host with mTLS authenticating cross-host
  traffic. Operators who want to scale `wolf-database` on a
  separate beefier host (Postgres benefits from this) can do
  so without changing application code.
* **Operator UX matches Wazuh's.** Same systemd verbs, same
  install-path layout, same per-component config dirs.
* **Future relay phase has zero new infrastructure to build.**
  The mTLS middleware shipped in 5.6 already accepts
  `LeafKind.CLIENT` certs. The relay phase just adds a
  `wolf-cert issue-relay <tenant>` subcommand to mint the
  relay's client cert.
* **The dev environment stays viable throughout.** The repo
  layout still supports `uv sync` + dev-local cert minting; the
  packaging deferral keeps us out of `.deb` rebuild hell during
  iteration.

### Negative

* **Substantial refactor.** Phase 5.5 alone touches every Python
  import (`from app.config` → `from wolf_server.config`,
  `services/orchestrator/...` → `services/server/...`),
  every doc, every commit-message convention, every test-discovery
  path. Mechanical but large.
* **`wolf-database` bundling has real complexity.** Wrapping
  `postgresql.service` with a Wolf-managed unit means we own
  the data-dir layout, the postgresql.conf, the pg_hba.conf, the
  upgrade story between Postgres versions. Wazuh-indexer has a
  whole team behind it; Wolf-database is one person. The 5.7
  scope must stay focused on "thin wrapper + migrations + the
  bare minimum config" rather than reimplementing every Postgres
  ops tool.
* **The dashboard becomes the bottleneck for every API request.**
  Reverse-proxying through Next.js adds a hop. For Wolf's traffic
  shape (small JSON, infrequent requests, streaming SSE) this is
  negligible, but we should keep an eye on streaming latency
  under load.
* **Packaging deferral means dev = production gap.** Operators
  who try Wolf before 5.9 have to install via the dev path
  (`git clone` + `uv sync` + `npm install`). The systemd story
  works (5.8) but distribution doesn't (5.9+). Acceptable
  trade-off: we'd rather iterate on the architecture under dev-
  install conditions than freeze it into a `.deb` we'll have to
  re-cut.

### Risks

* **Renaming touches everything.** Phase 5.5's blast radius is
  the whole repo. We mitigate by doing it as a single coherent
  slice with the full integrity gate, no functional changes
  mixed in.
* **mTLS misconfiguration produces silent failures.** A leaf
  cert with the wrong EKU, a missing CA on a client, a SAN
  mismatch — all of these surface as cryptic TLS errors at
  runtime, far from the configuration that caused them. We
  mitigate by adding explicit pre-flight checks at component
  startup (`resolve_tls()` is the prototype) plus logging the
  exact peer cert subject / issuer / fingerprint on every
  rejected connection.
* **Postgres-bundling is irreversible-ish.** Once we ship
  `wolf-database` as a managed component, operators expect it
  to stay that way. If we later decide bundling was the wrong
  call (e.g., MSSPs want to use their own managed Postgres),
  the unbundling is messy. We mitigate by writing the
  `wolf-database.service` as a thin shell over `postgresql.service`
  — it doesn't fork Postgres, it just wraps it. Reverting means
  removing the wrapper, not extracting a fork.

## Alternatives considered

* **Trust-portal wizard (the original "5.4-f" idea).** Rejected
  on UX grounds by the operator. Acknowledged: it would have
  been easier to build, but it would have left Wolf with the
  two-origin architecture forever.
* **Reverse proxy in front of both components (Caddy / nginx /
  Traefik).** Same effective topology, but adds a moving piece
  to the install. The dashboard-as-edge approach uses Next.js's
  existing API route mechanism — no extra binary to install,
  no extra unit file. The Wazuh dashboard already plays this
  role for `wazuh-indexer`; we follow the same pattern.
* **A single combined service (`wolf-server` runs both UI and
  API).** Considered briefly. Rejected because (a) Next.js is
  a different runtime (Node) than the FastAPI brain (Python) —
  combining them creates a polyglot process, (b) the operator
  ask was explicitly for separate components per the Wazuh
  parallel, (c) different teams might want to scale them
  independently in distributed mode.
* **Real CA (Let's Encrypt / commercial).** Considered as a
  zero-trust-install alternative. Rejected because Wolf is
  designed for internal / LAN / air-gapped deployments where
  ACME and a public hostname aren't always available. Operators
  who DO have a public hostname can drop a Let's Encrypt cert
  into `/etc/wolf-dashboard/certs/cert.pem` and the same launcher
  picks it up — no Wolf-side change needed.
* **Per-component independent CAs.** Considered for stronger
  isolation. Rejected because cross-component mTLS becomes
  combinatorial (each component needs to trust each other CA),
  and the operator's explicit ask was *one CA*.

## How this gets verified

* **Phase 5.5** — every existing test still passes after the
  rename. The acceptance test is "no functional change."
* **Phase 5.6** — the user-reported `NetworkError` reproduction
  (open `https://localhost:3000/` after `wolf-cert init`, try to
  log in) succeeds end-to-end with one trust decision (the
  dashboard's cert). Direct `curl` to `wolf-server:8000` without
  a client cert is REFUSED at the TLS layer (not via 401 — via
  TLS handshake failure).
* **Phase 5.7** — `systemctl restart wolf-database` brings
  Postgres back up under Wolf's data dir, migrations are
  idempotent, `wolf-server` reconnects without intervention.
* **Phase 5.8** — a fresh Ubuntu 24.04 VM with `wolf` installed
  via the dev path can complete `systemctl start
  wolf-database wolf-server wolf-dashboard`, the operator can
  log in via browser, mTLS handshakes succeed across all three
  legs.

## Cross-references

* Cross-session memory: `native-https-and-wolf-cert.md` (Phase
  5.4 design), `wolf-knowledge-relay.md` (the future phase
  that consumes this architecture's mTLS substrate),
  `integrity-across-the-stack.md` (every change touched by
  this program of work must preserve cross-stack integrity),
  `no-unaddressed-errors.md` (the `NetworkError` is the
  archetypal "error to address, not paper over").
* Existing ADRs: 0007 (native delivery channel commits to
  `.deb`/`.rpm` + systemd — this ADR cashes that in), 0008
  (native delivery is primary — this ADR commits to it
  operationally).
* Commits this ADR depends on: 9a44b65, 80e0f10, 5afd4e9,
  c7fed44, b064b82 (Phase 5.4 substrate), 8205e3f (Phase 5.4
  close-out + this direction recorded in PROGRESS / CHANGELOG).
