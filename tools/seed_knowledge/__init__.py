"""Phase 3 Slice 3 — real seed corpora.

Ingests MITRE ATT&CK techniques (from MITRE/CTI STIX JSON) and Wazuh
ruleset definitions (from the wazuh-ruleset XML mirror) into the
knowledge_chunks table. Replaces the 9-chunk inline dev seed from
`app.management.seed_dev_knowledge` with corpus material that's
representative of what a real Wolf deployment would carry.

Per doc 06 §Chunk on structure, not character count: one technique
per chunk, one rule per chunk.
"""
