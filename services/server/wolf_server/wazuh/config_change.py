"""config_change operation catalog + ossec.conf section editing (6-e.4 → 6-f.4).

``config_change`` edits the manager's ``ossec.conf`` — the LAST and highest-
blast-radius action class: a malformed configuration can take the manager down
for every org on the shared cluster.  6-f.4 (ADR 0032 B) generalized the v1
allowlist into free-form-within-rails:

  - **Blocklist, not allowlist** (B3): any well-formed section is authorable
    EXCEPT the break-the-manager set (:data:`BLOCKED_SECTIONS` — breaking
    enrollment, cluster membership, indexer connectivity or the ruleset loader
    can lock Wolf out of its own manager, so those stay hand-edited).
  - **Single-instance sections** (``update_section``): a full replacement
    ``<section>…</section>`` block; if the section is ABSENT the edit is an
    ADD (inserted before the file's final ``</ossec_config>``).
  - **Repeated / merge-semantic sections** (B2, ``upsert_block`` /
    ``remove_block``): one INSTANCE is addressed by **block-identity** — never
    by position.  ``block_key`` matches the instance's identity element first
    (:data:`IDENTITY_KEYS`: ``<integration>`` → ``<name>``, ``<localfile>`` →
    ``<location>``, ``<command>`` → ``<name>``); when that yields nothing it
    falls back to ANY leaf-element value that selects the instance (6-f.5 —
    e.g. a ``<hook_url>`` or ``<api_key>`` when three integrations share a
    ``<name>``).  Selection must be UNIQUE: >1 match refuses, and the refusal
    enumerates each instance's discriminating fields
    (:func:`describe_instances`) so the caller can re-address precisely.
    This is the ``<integration><name>virustotal</name>`` fix plus the 6-f.5
    duplicate-name fix: add/update/remove of a specific instance is precise
    and reversible even among same-name blocks.
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
import textwrap

# Operations.
OP_UPDATE_SECTION = "update_section"  # replace (or add) one single-instance section
OP_UPSERT_BLOCK = "upsert_block"  # add/replace ONE identity-keyed instance (B2)
OP_REMOVE_BLOCK = "remove_block"  # remove ONE identity-keyed instance (B2)
OP_RESTORE_CONFIG = "restore_config"  # reversal-only: restore the file snapshot

# Forward ops a propose tool / validator may accept (restore is reversal-only and
# is created via create_reversal_proposal, which bypasses the structural validator).
CONFIG_CHANGE_FORWARD_OPS = frozenset({OP_UPDATE_SECTION, OP_UPSERT_BLOCK, OP_REMOVE_BLOCK})

# Approver-facing phrasing.
OP_LABELS: dict[str, str] = {
    OP_UPDATE_SECTION: "Update configuration section",
    OP_UPSERT_BLOCK: "Add/update configuration block",
    OP_REMOVE_BLOCK: "Remove configuration block",
    OP_RESTORE_CONFIG: "Restore configuration",
}

# The break-the-manager set (B3): a bad edit here can break enrollment, cluster
# membership, indexer connectivity or the ruleset loader — an unrecoverable
# state that can lock Wolf out of its own manager.  These stay hand-edited;
# EVERYTHING else is authorable (blocklist, not allowlist — ADR 0032 B3).
BLOCKED_SECTIONS = frozenset({"auth", "cluster", "indexer", "rule_test", "ruleset"})

# The PRIMARY identity element per repeated / merge-semantic section (B2): the
# child element whose text is the instance's natural stable key.  Since 6-f.5
# this is the preferred key, not the only one — any uniquely-identifying leaf
# value also addresses an instance (see find_identified_blocks), so sections
# outside this map are addressable too when a unique leaf value exists.
IDENTITY_KEYS: dict[str, str] = {
    "command": "name",
    "integration": "name",
    "localfile": "location",
}

# Section names are lowercase XML element names in every stock ossec.conf.
_SECTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def is_valid_section_name(section: str) -> bool:
    """Syntactic validity of a section (element) name — the pre-check before any
    regex is built from it."""
    return bool(_SECTION_NAME_RE.fullmatch(section or ""))


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


def normalize_block_indent(block: str) -> str:
    """The canonical display/comparison form of one extracted XML snippet.

    A block regex match starts AT ``<section``, so the extracted first line
    carries no leading whitespace while every following line keeps its file
    indentation — quoted verbatim (previews, code fences) the opening and
    closing tags misalign.  Keep line 1 at column 0 and dedent the tail by
    ITS OWN common leading whitespace, preserving the relative structure.
    Idempotent.  Also the equality form for frozen-vs-live staleness, so a
    purely cosmetic indentation difference can never flunk (or fake) a
    freshness check."""
    text = (block or "").strip()
    first, sep, rest = text.partition("\n")
    if not sep:
        return first
    return first + "\n" + textwrap.dedent(rest)


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


def insert_section_block(raw: str, new_block: str) -> str | None:
    """Insert ``new_block`` before the LAST ``</ossec_config>`` in ``raw`` — the
    ADD path for a section/instance that is not in the file yet.  Returns the
    new file content, or ``None`` when the file has no closing wrapper to
    anchor on (a config that malformed is not something to append to)."""
    anchor = (raw or "").rfind("</ossec_config>")
    if anchor < 0:
        return None
    return raw[:anchor] + "  " + new_block.strip() + "\n\n" + raw[anchor:]


def identity_of(section: str, block: str) -> str | None:
    """The block-identity key of one ``<section>`` instance — the text of its
    identity element (e.g. ``<name>virustotal</name>`` inside an
    ``<integration>`` block).  ``None`` when the section has no identity
    element defined or the block does not carry one."""
    key_element = IDENTITY_KEYS.get(section)
    if key_element is None:
        return None
    match = re.search(
        rf"<{re.escape(key_element)}>\s*(.*?)\s*</{re.escape(key_element)}>",
        block or "",
        re.DOTALL,
    )
    return match.group(1).strip() if match else None


# Leaf elements only — no nested markup, no attributes.  The pool of candidate
# identifying values inside one section instance (<name>, <hook_url>, <api_key>,
# <location>, …).  Container elements (whose body holds further tags) are not
# identifying values and are excluded by the [^<] body.
_LEAF_ELEMENT_RE = re.compile(r"<([a-z][\w.-]*)>\s*([^<]*?)\s*</\1>", re.DOTALL)


def element_entries(block: str) -> list[tuple[str, str]]:
    """Every ``(tag, value)`` leaf element in one section instance, in document
    order — the candidate identifying values for block addressing and for the
    discriminating-field guidance in ambiguity refusals."""
    return [(m.group(1), m.group(2).strip()) for m in _LEAF_ELEMENT_RE.finditer(block or "")]


def carries_value(block: str, value: str) -> bool:
    """Whether ANY leaf element of ``block`` has exactly ``value`` as its text
    (stripped).  The 6-f.5 fallback addressing: a <hook_url>/<api_key>/… value
    selects an instance when the primary identity key is ambiguous or absent."""
    wanted = (value or "").strip()
    if not wanted:
        return False
    return any(v == wanted for _, v in element_entries(block))


def content_carries_key(section: str, content: str, block_key: str) -> bool:
    """Whether proposed ``content`` carries the ``block_key`` it addresses —
    either as the section's identity element or as any leaf value.  The
    validator's no-address-X-write-Y guard (an upsert must keep the value it
    used to address the instance, or it could silently retarget)."""
    wanted = (block_key or "").strip()
    if not wanted:
        return False
    return identity_of(section, content) == wanted or carries_value(content, wanted)


def _identified_matches(raw: str, section: str, block_key: str) -> list[re.Match[str]]:
    """The regex matches (spans + text) selected by ``block_key`` — primary
    identity-element equality first; when the identity key selects nothing,
    fall back to any-leaf-value equality.  Shared by every keyed operation so
    tool preview, executor write and persistence proof all agree on which
    instance a key addresses."""
    wanted = (block_key or "").strip()
    matches = list(_section_block_re(section).finditer(raw or ""))
    primary = [m for m in matches if identity_of(section, m.group(0)) == wanted]
    if primary:
        return primary
    return [m for m in matches if carries_value(m.group(0), wanted)]


def find_identified_blocks(raw: str, section: str, block_key: str) -> list[str]:
    """Every ``<section>`` instance selected by ``block_key`` — identity-element
    equality first, any-leaf-value fallback (6-f.5).  The caller enforces the
    exactly-one / exactly-zero rules per operation."""
    return [m.group(0) for m in _identified_matches(raw, section, block_key)]


def describe_instances(blocks: list[str]) -> str:
    """Guided-refusal enumeration for an ambiguous key: for each instance, the
    leaf fields whose value no OTHER instance shares — the usable
    discriminators (e.g. ``<hook_url>``/``<api_key>`` when three
    ``<integration>`` blocks share a ``<name>``).  Empty string when the
    instances are truly indistinguishable (no leaf value is unique), which is
    the genuine hand-fix case."""
    entries = [list(dict.fromkeys(element_entries(b))) for b in blocks]
    carriers: dict[tuple[str, str], int] = {}
    for entry in entries:
        for pair in entry:
            carriers[pair] = carriers.get(pair, 0) + 1
    parts: list[str] = []
    for i, entry in enumerate(entries, start=1):
        uniques = [f"<{t}>{v}</{t}>" for t, v in entry if v and carriers[(t, v)] == 1]
        if uniques:
            parts.append(f"instance {i}: " + ", ".join(uniques))
    return "; ".join(parts)


def upsert_identified_block(raw: str, section: str, block_key: str, new_block: str) -> str | None:
    """Replace the SINGLE ``<section>`` instance selected by ``block_key`` with
    ``new_block`` — or ADD it (before the final ``</ossec_config>``) when no
    instance matches.  Returns the new file content, or ``None`` when the key
    selects more than one instance (ambiguous — re-address by a unique field)
    or the add-anchor is missing."""
    matches = _identified_matches(raw, section, block_key)
    if len(matches) > 1:
        return None
    if len(matches) == 1:
        start, end = matches[0].span()
        return raw[:start] + new_block.strip() + raw[end:]
    return insert_section_block(raw, new_block)


def remove_identified_block(raw: str, section: str, block_key: str) -> str | None:
    """Remove the SINGLE ``<section>`` instance selected by ``block_key``.
    Returns the new file content, or ``None`` unless exactly one instance
    matches (nothing to remove / ambiguous are both the caller's refusal)."""
    matches = _identified_matches(raw, section, block_key)
    if len(matches) != 1:
        return None
    start, end = matches[0].span()
    # Take the line's leading indent + trailing newline with the block so the
    # removal doesn't leave a blank hole.
    while start > 0 and raw[start - 1] in " \t":
        start -= 1
    if end < len(raw) and raw[end] == "\n":
        end += 1
    return raw[:start] + raw[end:]


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


def build_candidate(raw: str, op: str, section: str, block_key: str, new_block: str) -> str | None:
    """The whole-file result of applying one forward op to ``raw`` — the SINGLE
    transformation used by BOTH the propose tool's author-time dry-run and the
    executor's real write, so what was previewed is exactly what runs.
    ``None`` when the change does not apply cleanly (ambiguous target /
    missing insertion anchor)."""
    if op == OP_UPDATE_SECTION:
        if find_section_blocks(raw, section):
            return replace_section_block(raw, section, new_block)
        return insert_section_block(raw, new_block)
    if op == OP_UPSERT_BLOCK:
        return upsert_identified_block(raw, section, block_key, new_block)
    if op == OP_REMOVE_BLOCK:
        return remove_identified_block(raw, section, block_key)
    return None


def block_persisted(raw: str, section: str, block_key: str, proposed_block: str) -> bool:
    """Authoritatively confirm an upsert stuck: exactly one ``<section>``
    instance in ``raw`` carries ``block_key`` and it matches ``proposed_block``
    ignoring the manager's reindentation (see :func:`_normalize_section`)."""
    blocks = find_identified_blocks(raw, section, block_key)
    if len(blocks) != 1:
        return False
    return _normalize_section(blocks[0]) == _normalize_section(proposed_block)


def block_removed(raw: str, section: str, block_key: str) -> bool:
    """Authoritatively confirm a removal stuck: NO ``<section>`` instance in
    ``raw`` carries ``block_key`` any more."""
    return len(find_identified_blocks(raw, section, block_key)) == 0


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
