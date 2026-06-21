---
name: wazuh-active-response-contract
description: "REFERENCE (2026-06-19): source-grounded Wazuh active-response (AR) invocation contract for PUT /active-response — accepted fields, the !-prefix, alert.data.srcip/dstuser, no per-call timeout, HTTP-200-on-failure. Verified on live v4.14.3 + AR source v4.14.3/v4.14.5. Fixes the 6-b firewall-drop 400 bug."
metadata:
  node_type: memory
  type: reference
---

How Wolf must drive Wazuh active response via the Server API. Verified
empirically on the live cluster (**v4.14.3**) AND against the AR script source on
GitHub across **v4.14.3 + v4.14.5** (latest 4.x, not v5.0 — sources identical
except `netsh.c`'s internal rule build, which doesn't change the input contract).

**`PUT /active-response?agents_list=<id>` accepts ONLY** `command`, `arguments`,
`alert`. `custom`, `timeout`, `location` → **HTTP 400 "Invalid field found {…}"**
(this was the 6-b bug: the write client sent `custom:false` → every run failed).

- `command` must be **`!`-prefixed** (`!firewall-drop`) to run a named command
  NOW (bare name = rule/custom lookup).
- Target rides in the alert the script reads (shared helpers in
  `src/active-response/active_responses.c`): srcip blockers (firewall-drop,
  host-deny, route-null, netsh, win_route-null, firewalld-drop, ipfw/npf/pf,
  ip-customblock) read **`parameters.alert.data.srcip`**, validated numeric
  IPv4/IPv6 by `get_ip_version`; **disable-account** reads
  **`parameters.alert.data.dstuser`** (`passwd -l`/`-u`, reversible);
  **restart-wazuh** reads neither.
- **No per-call timeout** — reversal (`add`→`delete`) is config-side
  (ossec.conf `<active-response><timeout>`); Wolf can't promise "block for N s".
- **HTTP 200 even on failure**: success = `total_affected_items>=1` AND no
  `failed_items`; the manager does NOT validate the command name (unknown → no-op
  on host). "Dispatched to agent" ≠ "applied on host" (no synchronous read-back).

**How to apply:** `wolf_server/wazuh/active_response.py` is the single source of
truth — the `AR_COMMANDS` catalog (platform/target/reversible), `build_ar_body`
(correct body), `classify_os`/`is_valid_ip`, `interpret_ar_result` (honest
verification). The validator is catalog-driven (require valid srcip / non-empty
username per command; **lenient** platform check — refuse only a *confirmed*
mismatch, never unknown OS, per [[wolf-unrestricted-full-power]]'s 6-a.1
no-false-refusal lesson). Full catalog + correlations:
`docs/reference/wazuh-active-response.md`. Reconciling the catalog against the
live `GET /manager/configuration?section=command` is a tracked enrichment.
