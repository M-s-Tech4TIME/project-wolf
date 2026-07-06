---
name: block-disambiguation-any-unique-field
description: "OPERATOR FEEDBACK (2026-07-06, 6-f.4 web-test; SHIPPED same day in 6-f.5): duplicate-name config blocks must be disambiguated by ANY uniquely-identifying child field (hook_url, api_key, …), and ambiguity refusals must enumerate the distinguishing fields so the model can self-correct"
metadata:
  type: feedback
---

**Feedback (2026-07-06, 6-f.4 web-test):** the manager had 3 `<integration>` blocks all named "custom-tracecat" → every `upsert_block` attempt was refused as ambiguous, even though hook_url and api_key clearly distinguished the instances. The model even tried `block_key=<the hook_url>` (the right instinct, visible in the citation JSON) and was rejected because identity matching reads ONLY `IDENTITY_KEYS` (integration→name). Starved of guidance, it then hallucinated "identical names and hook_urls". Operator: "there were two most clearly distinguishable fields… I expected wolf to catch this smartly, but it failed."

**Why:** name-only identity fails on real-world duplicate-name configs; and a refusal that doesn't say WHAT distinguishes the matched instances leaves the model unable to self-correct — the ambiguity refusal itself caused the hallucination.

**How to apply:** extend block matching so `block_key` may be any direct-child element value that uniquely selects ONE live instance (primary `IDENTITY_KEYS` lookup first; unique-field fallback second); the >1-match refusal must enumerate each instance's distinguishing child values and tell the model to re-call with one of them; extend the validator's identity-carry guard to accept content containing the addressing value; only truly indistinguishable duplicates remain a hand-fix refusal. Code: `wazuh/config_change.py` (`find_identified_blocks`/`identity_of`), `tools/propose_config_change.py` (`_capture_current`), `gateway/validator.py`. Guided-message discipline per [[scope-and-validation-discipline]].

**SHIPPED 2026-07-06 (slice 6-f.5, ADR 0032 addendum):** one shared `_identified_matches` selector (identity first, leaf-value fallback — `element_entries`/`carries_value`) behind `find_identified_blocks`, so tool capture / `build_candidate` / executor / persistence proofs agree; B2 ops on ANY unblocked section; `content_carries_key` identity-carry (to change the addressing field, address by another); `describe_instances` enumeration in the refusals; unit-pinned on the exact 3×custom-tracecat scenario.
