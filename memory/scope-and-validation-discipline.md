---
name: scope-and-validation-discipline
description: "STANDING RULE (2026-06-18, distilled from Phase 6.6): interrogate EVERY parameter/field/check/opt-in/scope out-of-the-box — no possible scope left unexplored; verify real-system behavior empirically before designing; emit a precise guided message per failure mode; and ensure every input that affects behavior is faithfully REFLECTED in its observable surfaces (a 'test' result must change when the thing it tests changes). Apply heavily wherever validation / input-handling / exception-handling / user-messaging / scope-mapping is involved."
metadata:
  node_type: memory
  type: feedback
---

Operator reflection after Phase 6.6 (Superuser-owned Wazuh mapping), 2026-06-18:
that phase taught deep RBAC config on BOTH Wolf's and Wazuh's sides, but more
importantly a *way of working* — think out-of-the-box about every single
parameter, field, check, opt-in, scope; leave **no possible scope unexplored**;
and implement/handle each accordingly. Carry this into all future phases,
**especially** where parameter/field validation, input validation, exception
handling, right-message prompting, and scope exploration/mapping are heavy.

**Why:** every 6.6 gap was caught only by refusing to assume — by interrogating
each field for its full range of states. Concrete exemplars to emulate:
- **Don't assume one instance of a thing.** The indexer probe checked ONE index;
  reworked to check EACH configured pattern, distinguishing every outcome
  (200+shards→readable / 0-shards→"no readable index" / 404→not found /
  403→denied / 401→bad creds) with a precise message per case.
- **Validate a feature's actual use-case, then MAP the validation back into the
  product surface.** The opt-in group-label filter was validated against a real
  broad/no-DLS credential (admin: all→scoped); we then realised "Test & Save"
  didn't reflect the opt-in at all, and made the probe run THROUGH the same
  filter so the result changes when the toggle changes.
- **Reflect every behavior-affecting input in its observable surface.** A toggle
  / field that changes what the system does MUST visibly change the test/result
  output — otherwise the user can't trust or verify it.
- **Derive truth from the authoritative source, not incidental data.** Scope came
  from the credential's own RBAC (`/security/users/me/policies`), not the
  agents' incidental group membership.
- **Guard state transitions, not just first-entry.** Changing a username with a
  blank password must 422, not silently reuse the old credential.

**How to apply — for each parameter / field / opt-in / scope, ask:**
1. What are ALL its possible states/values/paths (incl. empty, changed,
   multiple, malformed, forbidden, not-found, partial)? Enumerate + handle each.
2. What does the REAL system actually return for each path? Verify empirically
   (probe the live system) before designing logic or messages — don't assume
   (e.g. 401 vs 403 vs 404 vs 200/0-shards under `do_not_fail_on_forbidden`).
3. Does each failure/edge get a precise, guided, field-relevant message
   (server-authoritative + client-inline)? See [[input-validation-exception-handling]].
4. Does every input that AFFECTS behavior get faithfully reflected in the
   observable result (test/probe/preview)? If a toggle changes nothing visible,
   that's a bug.
5. Is there an unexplored scope/leak (a path that bypasses the intended bound)?
   Map it and close it (cf. the `read *` leak surface, the index-access check).

Reinforces [[no-unaddressed-errors]] + [[quality-secure-coding-discipline]];
the validation/messaging half is [[input-validation-exception-handling]].
