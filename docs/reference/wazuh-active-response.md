# Wazuh Active Response — invocation contract & command catalog

> **Source-grounded reference** for how Wolf drives Wazuh active response (AR)
> via the Server API. Verified against the **live cluster (v4.14.3)** by
> empirical API probing AND against the AR script source on GitHub across
> **v4.14.3 and v4.14.5** (latest 4.x; not v5.0). The AR sources are identical
> between those two tags except `netsh.c`'s *internal* rule construction, which
> does not change the input contract. Code: `wolf_server/wazuh/active_response.py`.

## 1. The API contract — `PUT /active-response?agents_list=<id>`

Accepted body fields (anything else → HTTP 400 `"Invalid field found {…}"`):

| Field | Type | Meaning |
|---|---|---|
| `command` | string (**required**) | The AR command. **Must be `!`-prefixed** (`!firewall-drop`) to *run a named command immediately*; the bare name is a rule/custom lookup. |
| `arguments` | string[] | Extra CLI args (`extra_args`). Unused by the default blockers — they take their target from `alert`. |
| `alert` | object | The alert the script reads its target from (see §3). |

**Rejected fields (verified live):** `custom`, `timeout`, `location`. The
original 6-b write client sent `custom: false` → **every run failed at the API**
(this was the bug). There is **no per-call timeout** — the reversal window is
config-side (ossec.conf `<active-response><timeout>`), so Wolf cannot honour an
arbitrary "block for N seconds" through the API; it runs the configured command.

**Response shape — HTTP 200 even on failure.** Success ≠ status code. A run is
*dispatched* iff `data.total_affected_items >= 1` and `data.failed_items` is
empty. Failures (agent offline/missing, etc.) return `200` with `error: 1` and
`failed_items[].error.message`. The manager does **not** validate the command
name against the configured set — an unknown command resolves the agent and then
no-ops on the host. "Dispatched to the agent" is **not** "applied on the host":
AR has no synchronous read-back, so Wolf's verification is honest about that.

## 2. Command string format

`"command": "!<name>"`. The `!` prefix means "run this exact named command now"
(bypassing rule matching). `build_ar_body` always normalises to a single `!`.

## 3. The unified input model (shared helpers)

All default scripts read their input through shared helpers in
`src/active-response/active_responses.c`, so the contract is **uniform**:

- `get_command_from_json` → top-level `command`: `"add"` (ADD_COMMAND) or
  `"delete"` (DELETE_COMMAND); anything else aborts. A `!command` API call maps
  to `add`; the timeout-driven reversal is the manager re-invoking with `delete`.
- `get_srcip_from_json` → **`parameters.alert.data.srcip`** (also Windows
  `win.eventdata`). Validated by `get_ip_version` → `getaddrinfo(AI_NUMERICHOST)`:
  **numeric IPv4/IPv6 only**, else the script aborts. Wolf mirrors this with
  `is_valid_ip` (Python `ipaddress`) at propose time — bad IPs never dispatch.
- `get_username_from_json` → **`parameters.alert.data.dstuser`**.
- `get_extra_args_from_json` → `parameters.extra_args` (the `arguments` field).

So `build_ar_body(command, srcip=…, username=…, arguments=…)` covers every
command's needs by setting `alert.data.srcip` / `alert.data.dstuser`.

## 4. Command catalog (default AR scripts)

Target = what the command acts on. Reversible = supports `add`/`delete`
(timeout reversal). "On cluster" = present in this cluster's `<command>` config.

| Command | Platform | Target | Reversible | Effect | On cluster |
|---|---|---|---|---|---|
| `firewall-drop` | Linux | srcip | yes | iptables DROP the IP | ✅ (in catalog) |
| `firewalld-drop` | Linux | srcip | yes | firewalld drop the IP | ❌ |
| `host-deny` | Linux/Unix | srcip | yes | append `ALL:<ip>` to `/etc/hosts.deny` | ✅ |
| `route-null` | Linux/macOS | srcip | yes | null/blackhole route the IP (`route add <ip> reject`) | ✅ |
| `ip-customblock` | Linux/Unix | srcip | varies | user-customisable block | ❌ |
| `ipfw` / `npf` / `pf` | BSD/macOS | srcip | yes | BSD-firewall drop the IP | ❌ |
| `netsh` | Windows | srcip | yes | `netsh advfirewall` block rule (dir=in & out) | ✅ |
| `win_route-null` | Windows | srcip | yes | null-route the IP (Windows) | ✅ |
| `disable-account` | Linux/macOS | username | yes | `passwd -l` (lock) / `-u` (unlock); AIX `chuser` | ✅ |
| `restart-wazuh` | all | none | n/a | `bin/wazuh-control restart` | ✅ |
| `yara_linux[2]` | Linux | (extra_args) | no | YARA scan; FIM-triggered, `-yara_path`/`-yara_rules` args | ✅ (excluded¹) |
| `wazuh-slack` | all | srcip | no | Slack notification (not enforcement) | ❌ |
| `kaspersky` | Linux | — | varies | Kaspersky integration | ❌ |

¹ `yara_*` is a detection/scan AR driven by `extra_args` on FIM rules, not a
manual srcip/username enforcement action — out of scope for the propose flow.

**Wolf's active catalog** (`AR_COMMANDS`) = the *enforcement/admin* commands:
`firewall-drop`, `host-deny`, `route-null`, `disable-account`, `restart-wazuh`,
`netsh`, `win_route-null`, the BSD blockers `pf` / `ipfw` / `npf`, and the OPNsense
appliance blocker `opnsense-fw` (6-c.2a). Each carries platform + target +
**base-severity** metadata (block = High, disable-account = Medium, restart = Low),
and Wolf offers a command only on a platform it fits. Per-OS `block_ip` selection
(6-c.2a): Linux→`firewall-drop`, Windows→`netsh`, macOS→`pf`, FreeBSD/OpenBSD→`pf`
(→`ipfw` pre-FreeBSD-5.3 / pre-macOS-10.7), NetBSD→`npf`, **OPNsense/pfSense→
`opnsense-fw`**. There is **no** manager-config presence check — an AR is just a
`PUT /active-response` call (ADR 0027 §2).

> **OPNsense note (verified 6-c.2a):** the OPNsense-native `opnsense-fw` does
> `pfctl -t __wazuh_agent_drop -T add` + `pfctl -k` against the table OPNsense's
> built-in rule blocks, so the IP actually lands in the blocklist. Stock `pf`
> uses a different (unreferenced) table → it dispatches but never applies. Its AR
> run isn't decoded into a dashboard alert (custom format vs `ar_log_json`/rule
> 657) — observability only; enforcement is confirmed.

## 5. How Wolf uses this (failproof path)

1. **Propose** (`propose_active_response`): the model passes a high-level
   **intent** (`block_ip` / `disable_user` / `restart`) + `srcip`/`username` —
   never a low-level command. Wolf resolves the agent's OS and
   **deterministically selects the platform-correct command** from the catalog
   (slice 6-c: `block_ip` → firewall-drop on Linux, netsh on Windows, route-null
   on macOS), resolves the agent's groups (capability), and freezes the intent +
   resolved command + structured params into the content-hashed proposal. An
   intent Wolf can't map to a command (OS unknown, or unsupported on the OS — e.g.
   `disable_user` on Windows) is refused with guidance, never executed blind.
2. **Validate** (hard gate): command ∈ catalog; required target present and
   well-formed (`srcip` a valid IP, `username` non-empty); **platform check** —
   now a defense-in-depth backstop (6-c already selected a platform-correct
   command), still lenient: refuse only a *confirmed* mismatch, never on an
   unknown OS (no false refusals — the 6-a.1 lesson).
3. **Approve + execute**: capability re-checked (group-aware), body built by
   `build_ar_body` (`!`-prefix, `alert.data.*`, no `custom`/`timeout`), issued
   by the bounded write client.
4. **Verify** (`interpret_ar_result`): dispatched iff affected≥1 and no
   `failed_items`; surfaces the failure message otherwise; honest that dispatch
   ≠ host-applied.

## 6. Notes / follow-ons

- Per-call timeout is not API-expressible; reversal is config-side. If a future
  need arises, it's a manager-config change, not an API field.
- Reconciling `AR_COMMANDS` against the *live* `GET /manager/configuration?
  section=command` (so the offered set tracks the cluster exactly) is a tracked
  enrichment.
- `disable-account` username path (`data.dstuser`) and srcip path (`data.srcip`)
  are confirmed from source; a live execution smoke on a disposable agent (smoke
  b) remains the final end-to-end confirmation.
