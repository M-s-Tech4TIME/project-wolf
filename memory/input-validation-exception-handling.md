---
name: input-validation-exception-handling
description: "STANDING RULE (2026-06-15) — every input field project-wide must have input validation + guided, content-relevant exception handling with readable error messages (server-authoritative + client-inline); a dedicated slice retrofits pre-rule fields"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

STANDING RULE (2026-06-15): the operator directed — verbatim intent — "from now onwards, you must introduce and contain input validation as well as proper and guided exception handling for each and every single input field for this whole project," and that a separate slice/phase be dedicated to retrofitting every input field introduced BEFORE this rule.

**Why:** the 6.5-e.2 web-test caught forms rendering `[object Object]` instead of the real reason — `unwrap()` in `lib/api.ts` did `String(detail)` on FastAPI's 422 `detail`, which is a LIST of pydantic error objects. Errors must guide the user to the exact problem; every field must reject bad input with a relevant, readable message.

**How to apply — for EVERY input field, going forward:**
1. **Server-side validation is authoritative:** pydantic `Field(min_length/max_length/pattern=…)`, `EmailStr`, allowlists, etc. Never ship a bare `str` where the content is constrained (the `RecoveryAdminRequest.display_name` gap that prompted this is the cautionary example).
2. **Client-side mirrors it for inline UX** before the round-trip (e.g. `isValidEmail` in `lib/utils.ts`), and never relaxes the server rule.
3. **Error rendering is human-readable + field-relevant:** the shared `unwrap()`/`formatApiDetail` in `lib/api.ts` turns a 422 `detail` array into `"field: message"`. Always surface `ApiError.message`; never render an object.
4. **Add a test per constraint** (e.g. empty value → 422), per [[no-unaddressed-errors]].

**Dedicated retrofit slice:** fields introduced before 2026-06-15 (Phase 6.4/6.5-a…e.2 and earlier — login, chat composer, org CRUD, member mgmt, password resets, etc.) are audited + brought up to this bar in a tracked slice in `docs/10-build-roadmap.md`. Related: [[quality-secure-coding-discipline]], [[integrity-across-the-stack]].

**Broader discipline (from Phase 6.6, 2026-06-18):** validation/messaging is one
half — also interrogate EVERY parameter/field/opt-in/scope for its full range of
states, verify the real system's behavior empirically before designing, and make
every behavior-affecting input visibly reflected in its test/result surface. See
[[scope-and-validation-discipline]].
