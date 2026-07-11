"""Rule-tuning operation catalog + ``local_rules.xml`` override construction (6-e.3).

``rule_tuning`` fine-tunes EXISTING Wazuh rules — disable a noisy rule (set level
0) or adjust its alert level — by writing an ``overwrite="yes"`` override into the
single canonical custom file ``etc/rules/local_rules.xml``.  Wolf never edits the
stock ``ruleset/rules/`` files (Wazuh overwrites those on upgrade); tuning a stock
rule is done by an override here, which is also Wazuh's recommended pattern.

The original rule's matching conditions are PRESERVED — the override copies the
rule's inner body verbatim and only changes ``level`` + adds ``overwrite="yes"`` —
so a tuned rule keeps its firing semantics; only its alerting level changes.

rule_tuning is manager-GLOBAL (one file shared by every org), so it is gated by
the ``rules:update`` RBAC action, held only by a Superuser/admin credential (ADR
0029 §per-class scoping).  It is reversible by **snapshot-restore**: the executor
captures local_rules.xml before the write (``prior_state``) and the reversal PUTs
it back — a real undo, not wolf-pack-bound (ADR 0029 §2).

local_rules.xml is a *multi-root* fragment (several ``<group>`` siblings + XML
comments — NOT a single-root document), so these helpers are deliberately
string/regex based, never ElementTree.  Correctness of the emitted file is gated
at execution time by ``GET /manager/configuration/validation``: a file that does
not compile is auto-rolled-back and never applied.
"""

from __future__ import annotations

import re

from wolf_server.wazuh.config_change import normalize_block_indent

# Operations.
OP_DISABLE_RULE = "disable_rule"  # silence a rule (set level 0)
OP_ADJUST_LEVEL = "adjust_level"  # change a rule's alert level
OP_RESTORE_RULES = "restore_rules"  # reversal-only: restore the captured file snapshot

# Forward ops a propose tool / validator may accept (restore is reversal-only and
# is created via create_reversal_proposal, which bypasses the structural validator).
RULE_TUNING_FORWARD_OPS = frozenset({OP_DISABLE_RULE, OP_ADJUST_LEVEL})

# Approver-facing phrasing.
OP_LABELS: dict[str, str] = {
    OP_DISABLE_RULE: "Disable rule",
    OP_ADJUST_LEVEL: "Adjust rule level",
    OP_RESTORE_RULES: "Restore rules file",
}

# The canonical (and, in v1, only) file Wolf writes.
LOCAL_RULES_FILENAME = "local_rules.xml"
LOCAL_RULES_DIRNAME = "etc/rules"

# Wazuh alert levels span 0..16; level 0 = no alert (our "disable").
MIN_LEVEL = 0
MAX_LEVEL = 16
DISABLE_LEVEL = 0

# Wazuh rule ids are integers 1..999999.
_MIN_RULE_ID = 1
_MAX_RULE_ID = 999999

# The group Wolf wraps its overrides in (a stable marker for idempotent re-tuning).
WOLF_TUNING_GROUP = "wolf_tuning,"


def is_valid_rule_id(value: str) -> bool:
    """True for a syntactically valid Wazuh rule id (a positive integer ≤ 999999)."""
    s = (value or "").strip()
    return s.isdigit() and _MIN_RULE_ID <= int(s) <= _MAX_RULE_ID


def is_valid_level(level: object) -> bool:
    """True for a valid Wazuh alert level (an int 0..16; ``bool`` is rejected)."""
    if not isinstance(level, int) or isinstance(level, bool):
        return False
    return MIN_LEVEL <= level <= MAX_LEVEL


# ── local_rules.xml manipulation (string-based; multi-root fragment) ──────────

_OPEN_TAG_RE = re.compile(r"<rule\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_LEVEL_ATTR_RE = re.compile(r'\blevel\s*=\s*"[^"]*"', re.IGNORECASE)
_OVERWRITE_ATTR_RE = re.compile(r'\boverwrite\s*=\s*"[^"]*"', re.IGNORECASE)


def _rule_block_re(rule_id: str) -> re.Pattern[str]:
    # <rule ... id="X" ...> … </rule>  (non-greedy — rule elements do not nest).
    return re.compile(
        rf'<rule\b[^>]*\bid\s*=\s*"{re.escape(rule_id)}"[^>]*>.*?</rule>',
        re.IGNORECASE | re.DOTALL,
    )


def extract_rule_block(xml_text: str, rule_id: str) -> str | None:
    """The first ``<rule id="X">…</rule>`` block in ``xml_text`` (or ``None``).

    Used to copy a rule's exact body from its source file so the override
    preserves its matching conditions.  Indent-normalized: the match starts AT
    ``<rule`` (the first line loses the source file's leading whitespace while
    the tail keeps it), so the raw match misaligns when quoted or written —
    normalize_block_indent re-aligns it without touching the body."""
    match = _rule_block_re(rule_id).search(xml_text or "")
    return normalize_block_indent(match.group(0)) if match else None


def build_override_block(rule_block: str, *, level: int) -> str:
    """Rewrite a ``<rule>`` block as an override: set ``level`` + ``overwrite="yes"``,
    preserving the id, every other attribute, and the full inner body (the matching
    conditions) — so the rule keeps firing semantics and only its level changes."""
    stripped = rule_block.strip()
    match = _OPEN_TAG_RE.match(stripped)
    if match is None:  # pragma: no cover — caller passes a real block or the fallback
        return stripped
    attrs = match.group(1)
    rest = stripped[match.end() :]  # inner body + </rule>
    if _LEVEL_ATTR_RE.search(attrs):
        attrs = _LEVEL_ATTR_RE.sub(f'level="{level}"', attrs)
    else:
        attrs = attrs.rstrip() + f' level="{level}"'
    if _OVERWRITE_ATTR_RE.search(attrs):
        attrs = _OVERWRITE_ATTR_RE.sub('overwrite="yes"', attrs)
    else:
        attrs = attrs.rstrip() + ' overwrite="yes"'
    return f"<rule{attrs}>{rest}"


def minimal_override_block(rule_id: str, *, level: int, description: str) -> str:
    """A bare override used only when the source rule body cannot be recovered.

    Conditionless — it neutralises alerting (level 0) but loses matching; the
    config-validation gate confirms it compiles before anything is applied."""
    safe_desc = description.replace("<", "").replace(">", "").strip() or "Tuned by Wolf"
    return (
        f'<rule id="{rule_id}" level="{level}" overwrite="yes">'
        f"<description>{safe_desc}</description></rule>"
    )


def _marker(rule_id: str) -> str:
    return f"<!-- wolf-tuning:rule={rule_id} -->"


def _tuning_block_re(rule_id: str) -> re.Pattern[str]:
    return re.compile(
        rf"\n*{re.escape(_marker(rule_id))}\s*"
        rf'<group name="{re.escape(WOLF_TUNING_GROUP)}">.*?</group>',
        re.DOTALL,
    )


def has_override(local_rules_xml: str, rule_id: str) -> bool:
    """True iff a Wolf tuning override block for ``rule_id`` is present.

    The authoritative *"our write actually persisted"* check: the executor
    re-reads ``local_rules.xml`` after the PUT and confirms this marker is there
    before declaring success (``GET`` reflects the on-disk file immediately)."""
    return _marker(rule_id) in (local_rules_xml or "")


def strip_tuning_block(local_rules_xml: str, rule_id: str) -> str:
    """Remove any prior Wolf-managed override block for ``rule_id`` (idempotent
    re-tuning — a second tune of the same rule replaces the first, never stacks a
    duplicate rule id)."""
    return _tuning_block_re(rule_id).sub("", local_rules_xml or "")


def apply_override(local_rules_xml: str, rule_id: str, override_block: str) -> str:
    """Return new local_rules.xml carrying a single Wolf override for ``rule_id``.

    Any prior Wolf override for that id is removed first (idempotent); the new one
    is appended in a marked ``wolf_tuning`` group at the end of the file."""
    base = strip_tuning_block(local_rules_xml or "", rule_id).rstrip()
    block = (
        f"\n\n{_marker(rule_id)}\n"
        f'<group name="{WOLF_TUNING_GROUP}">\n'
        f"  {override_block}\n"
        f"</group>\n"
    )
    return base + block
