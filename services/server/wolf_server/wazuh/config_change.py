"""config_change operation catalog + ossec.conf section editing (6-e.4, ADR 0029).

``config_change`` edits the manager's ``ossec.conf`` — the LAST and highest-
blast-radius action class: a malformed configuration can take the manager down
for every org on the shared cluster.  v1 is deliberately narrow:

  - **Section-scoped, allowlisted edits only** (``update_section``): the model
    proposes a full replacement ``<section>…</section>`` block for ONE known,
    single-instance section.  Wolf refuses sections outside the allowlist and
    files where the section appears more than once (repeated sections are
    merge-semantic in Wazuh — replacing "the" block is ambiguous; the live
    stock file carries e.g. ``<global>`` ×2, ``<localfile>`` ×8).
  - **Highest-risk sections are excluded** (``cluster``/``auth``/``indexer``/
    ``ruleset``): breaking enrollment, cluster membership, indexer connectivity
    or the ruleset loader is the one category v1 keeps hand-edited.
  - The **diff is captured at propose time** (the current block rides in the
    proposal parameters) so the approver sees exactly old → new before
    approving, and the executor can detect a config that changed under the
    proposal (staleness).

Manager-GLOBAL scope: ``PUT /manager/configuration`` replaces the MASTER node's
``ossec.conf`` (worker nodes keep their own; the cluster does not sync it), and
the RBAC gate is ``manager:update_config`` — held only by a Superuser/admin
credential (a per-org credential can *read* the config but not write it; probed
live 2026-07-02).  Reversible by **snapshot-restore**: the executor captures the
whole file before the write (``prior_state``) and the reversal PUTs it back —
the same real-undo model as rule_tuning (ADR 0029 §2).

Like ``local_rules.xml``, ``ossec.conf`` is a *multi-root* XML fragment (several
``<ossec_config>`` blocks + comments), so these helpers are string/regex based,
never ElementTree.  Correctness is gated at execution time by
``GET /manager/configuration/validation`` with auto-rollback on failure.
"""

from __future__ import annotations

import re

# Operations.
OP_UPDATE_SECTION = "update_section"  # replace one allowlisted section block
OP_RESTORE_CONFIG = "restore_config"  # reversal-only: restore the file snapshot

# Forward ops a propose tool / validator may accept (restore is reversal-only and
# is created via create_reversal_proposal, which bypasses the structural validator).
CONFIG_CHANGE_FORWARD_OPS = frozenset({OP_UPDATE_SECTION})

# Approver-facing phrasing.
OP_LABELS: dict[str, str] = {
    OP_UPDATE_SECTION: "Update configuration section",
    OP_RESTORE_CONFIG: "Restore configuration",
}

# v1 editable sections — single-instance in the stock manager ossec.conf and
# realistic tuning targets (SCA / vuln-detection / FIM / log settings).  NOT
# included, deliberately:
#   - repeated-in-stock sections (global ×2, wodle, command, active-response,
#     localfile, integration) — "the" block is ambiguous under Wazuh's
#     merge-on-repeat semantics;
#   - the break-the-manager set (cluster, auth, indexer, ruleset, rule_test) —
#     v1 keeps those hand-edited.
EDITABLE_SECTIONS = frozenset(
    {
        "alerts",
        "logging",
        "remote",
        "rootcheck",
        "sca",
        "syscheck",
        "vulnerability-detection",
    }
)

# A proposed section block must stay reviewable — an approver reads it in the
# queue. 16 KB is ~4× the largest stock section.
MAX_SECTION_CHARS = 16_384


def _section_block_re(section: str) -> re.Pattern[str]:
    # <section ...> … </section> (non-greedy; same-name sections never nest).
    return re.compile(
        rf"<{re.escape(section)}(?:\s[^>]*)?>.*?</{re.escape(section)}>",
        re.DOTALL,
    )


def find_section_blocks(raw: str, section: str) -> list[str]:
    """Every ``<section>…</section>`` block in the raw ossec.conf, in order.

    The caller enforces the v1 exactly-one rule: ``0`` → nothing to edit,
    ``>1`` → ambiguous under merge semantics (both refused with guided
    messages)."""
    return _section_block_re(section).findall(raw or "")


def replace_section_block(raw: str, section: str, new_block: str) -> str | None:
    """Replace the SINGLE ``<section>`` block in ``raw`` with ``new_block``.

    Returns the new file content, or ``None`` unless the section appears
    exactly once (the caller has better context for the refusal message)."""
    pattern = _section_block_re(section)
    matches = list(pattern.finditer(raw or ""))
    if len(matches) != 1:
        return None
    start, end = matches[0].span()
    return raw[:start] + new_block.strip() + raw[end:]


def _normalize_section(block: str) -> str:
    """Indentation/whitespace-insensitive canonical form of a ``<section>`` block.

    The Wazuh Server API RE-SERIALISES ``ossec.conf`` when it writes it (it
    re-indents the file to its own house style), so the exact string Wolf PUT is
    NOT a byte-for-byte substring of the file read back afterwards.  A literal
    match therefore false-negatives a change that *did* apply — the live 6-e.4
    web-test failure ("<sca> block did not persist", twice, on a write that
    validated).  Collapsing inter-tag and internal whitespace compares structure
    + content while tolerating reformatting — the same robustness rule_tuning
    already gets from its structural ``has_override`` check (why it passed on the
    same cluster where config_change's substring check failed).  Kept string/
    regex based, never ElementTree, per this module's contract (multi-root
    fragment)."""
    collapsed = re.sub(r">\s+<", "><", (block or "").strip())
    return re.sub(r"\s+", " ", collapsed).strip()


def section_persisted(raw: str, section: str, proposed_block: str) -> bool:
    """Authoritatively confirm a write stuck: ``section`` appears exactly once in
    ``raw`` and matches ``proposed_block`` ignoring the manager's reindentation
    (see :func:`_normalize_section`).  ``0`` or ``>1`` occurrences → not
    persisted (the edit did not land as a single clean block)."""
    blocks = find_section_blocks(raw, section)
    if len(blocks) != 1:
        return False
    return _normalize_section(blocks[0]) == _normalize_section(proposed_block)


def is_valid_section_block(section: str, content: str) -> tuple[bool, str]:
    """Structural validity of a proposed replacement block, with a guided reason.

    The content must be exactly one ``<section …>…</section>`` element for the
    TARGET section (nothing before/after it), must not smuggle in an
    ``<ossec_config>`` wrapper or another top-level section, and must stay
    within the reviewable size cap.  This is a *shape* check — semantic
    validity is enforced at execution by the manager's own
    ``/manager/configuration/validation`` (with auto-rollback)."""
    text = (content or "").strip()
    if not text:
        return False, "Section content is empty — provide the full replacement block."
    if len(text) > MAX_SECTION_CHARS:
        return (
            False,
            f"Section content exceeds {MAX_SECTION_CHARS} characters — too large to review.",
        )
    if "<ossec_config" in text:
        return (
            False,
            "Section content must be a bare <section> block, not an <ossec_config> wrapper.",
        )
    match = _section_block_re(section).fullmatch(text)
    if match is None:
        return (
            False,
            f"Content must be exactly one <{section}>…</{section}> block "
            "(the full replacement for the section), with nothing outside it.",
        )
    return True, ""
