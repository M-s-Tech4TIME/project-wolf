"""query_runbook — RAG retrieval over stable-knowledge corpora.

Per doc 06: "metadata filters as first-class arguments, not as free-text
query content." Source type and entity filters (rule_id, technique) are
proper Pydantic fields the agent must populate explicitly; this keeps
retrieval deterministic and lets the agent narrow precisely.

Tenant scoping is enforced inside the KnowledgeStore — this tool passes
the tenant_id from the immutable TenantContext and never lets the model
override it (the dispatcher's sanitize_tenant_id_from_args strips any
model-supplied tenant_id before this code runs).
"""

from typing import Any

from pydantic import BaseModel, Field

from app.knowledge.store import ALL_SOURCE_TYPES
from app.tools.base import Citation, ReadTool, ToolExecContext


class QueryRunbookInput(BaseModel):
    """Inputs to the runbook / knowledge retrieval."""

    query: str = Field(
        description=(
            "Natural-language question to embed and search against the "
            "knowledge corpora (Wazuh docs, ATT&CK, tenant runbooks)."
        ),
        min_length=1,
    )
    source_types: list[str] | None = Field(
        default=None,
        description=(
            "Optional filter: restrict to specific corpora. Allowed values: "
            "'wazuh_doc', 'attack', 'runbook', 'past_incident'. Omit to "
            "search every corpus the tenant can see."
        ),
    )
    rule_id: int | None = Field(
        default=None,
        description=(
            "Optional Wazuh rule ID metadata filter (e.g. 5710). Narrows "
            "to chunks tagged with that rule."
        ),
    )
    technique: str | None = Field(
        default=None,
        description=(
            "Optional ATT&CK technique ID metadata filter (e.g. 'T1110'). "
            "Narrows to chunks tagged with that technique."
        ),
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of chunks to return. Default 5, max 20.",
    )


class KnowledgeHit(BaseModel):
    chunk_id: str
    source_type: str
    content: str
    chunk_metadata: dict[str, Any] = Field(default_factory=dict)
    # Cosine distance — lower means more similar. Surface for the agent
    # so it can weight relevance.
    distance: float


class QueryRunbookOutput(BaseModel):
    hits: list[KnowledgeHit]
    citation: Citation


class QueryRunbookTool(ReadTool):
    name = "query_runbook"
    description = (
        "Retrieve relevant stable-knowledge chunks (Wazuh docs, ATT&CK "
        "techniques, tenant runbooks and past-incident write-ups) for a "
        "question. Use this for product-knowledge or procedural questions "
        "(\"what does rule X do\", \"how do we respond to brute force\"). "
        "Do NOT use this for live state — for current alerts or agent "
        "status, use the dedicated Wazuh read tools."
    )
    InputModel = QueryRunbookInput
    OutputModel = QueryRunbookOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, QueryRunbookInput)
        if exec_ctx.knowledge_store is None:
            # Wired conditionally — tools that depend on it must surface
            # the configuration gap rather than fail silently.
            raise RuntimeError(
                "query_runbook invoked but knowledge_store is not configured "
                "on this ToolExecContext. Check chat.py wiring."
            )

        # Validate source_types up-front so the agent gets a useful Pydantic
        # error rather than a 500 from the store layer.
        if args.source_types:
            unknown = [
                st for st in args.source_types if st not in ALL_SOURCE_TYPES
            ]
            if unknown:
                raise ValueError(
                    f"Unknown source_type(s): {unknown}; allowed: "
                    f"{sorted(ALL_SOURCE_TYPES)}"
                )

        metadata_filters: dict[str, Any] = {}
        if args.rule_id is not None:
            metadata_filters["rule_id"] = str(args.rule_id)
        if args.technique is not None:
            metadata_filters["technique"] = args.technique

        retrieved = await exec_ctx.knowledge_store.search(
            tenant_id=exec_ctx.tenant.tenant_id,
            query_text=args.query,
            source_types=args.source_types,
            metadata_filters=metadata_filters or None,
            limit=args.limit,
        )

        hits = [
            KnowledgeHit(
                chunk_id=str(r.id),
                source_type=r.source_type,
                content=r.content,
                chunk_metadata=r.chunk_metadata,
                distance=r.distance,
            )
            for r in retrieved
        ]
        return QueryRunbookOutput(
            hits=hits,
            citation=self.make_citation(
                args.model_dump(mode="json", exclude_none=True),
                result_count=len(hits),
            ),
        )
