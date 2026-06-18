"""Tool base class, execution context, and citation primitive.

Every read tool subclasses `ReadTool`, declares its input/output Pydantic
models, and implements `run()`.  The runtime is bound to a Wazuh connection
(per request) and the organization context (per request).

Citation: every tool output carries one or more citation objects.  This is
the foundation for grounding (doc 06 §Hallucinated grounding): the agent's
final answer must trace every factual claim back to a citation.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from wolf_schema import ToolSchema, ToolTier

from wolf_server.guardrails.limits import ResourceLimits
from wolf_server.organization.context import OrganizationContext


class Citation(BaseModel):
    """A traceable pointer back to where a fact came from.

    The agent's grounding validator (Phase 3) uses citations to verify that
    every factual claim in a final answer maps to either a tool result or a
    retrieved knowledge chunk.
    """

    tool: str = Field(description="Tool that produced this result")
    query: dict[str, Any] = Field(
        default_factory=dict,
        description="Sanitized query arguments — credentials redacted",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the call was made",
    )
    result_count: int | None = Field(default=None, description="How many records")


@dataclass
class ToolExecContext:
    """Per-request execution context passed to every tool.

    Built by the dispatcher from the immutable OrganizationContext + resolved
    Wazuh connection.  Never constructed from model output.
    """

    organization: OrganizationContext
    limits: ResourceLimits
    # Wazuh clients are typed as `Any` here so subclasses can opt into the
    # one(s) they need without circular imports.  The dispatcher always
    # populates both.
    opensearch: Any
    server_api: Any
    # Phase 3 RAG store — typed Any to avoid an import cycle. Optional
    # because Phase 2 read-tool tests and the smoke CLI don't wire it.
    # query_runbook raises a clear error if invoked when this is None.
    knowledge_store: Any | None = None
    # Phase 4 Slice 3 — organization-scoped cache. Typed Any to avoid an
    # import cycle. Optional because tests can stub it; chat.py wires
    # the process-wide singleton from wolf_server.caching. Tools that want to
    # cache (e.g. agent_name → agent_id resolution) check for None
    # before using.
    cache: Any | None = None
    # Phase 6 (ADR 0025) — the request DB session. Optional + typed Any to
    # avoid an import cycle. Read tools never use it; PROPOSE tools need it to
    # persist the proposal they emit into the approval queue. The dispatcher
    # wires the live session; it commits with the rest of the request.
    db: Any | None = None


class ReadTool(ABC):
    """Abstract base class for all read-tier tools.

    Subclasses declare:
      - name: the canonical tool name (sent to the model).
      - description: one-sentence description used in the model's tool catalog.
      - InputModel: Pydantic model for argument validation.
      - OutputModel: Pydantic model for result validation.
      - run(): the actual execution logic.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    InputModel: ClassVar[type[BaseModel]]
    OutputModel: ClassVar[type[BaseModel]]
    tier: ClassVar[ToolTier] = ToolTier.read

    def schema(self) -> ToolSchema:
        """The canonical ToolSchema sent to the model in its tool catalog."""
        return ToolSchema(
            name=self.name,
            description=self.description,
            tier=self.tier,
            input_schema=self.InputModel.model_json_schema(),
            output_schema=self.OutputModel.model_json_schema(),
        )

    @abstractmethod
    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        """Execute the tool.  `args` is already a validated InputModel instance."""

    def make_citation(
        self,
        query: dict[str, Any],
        *,
        result_count: int | None = None,
    ) -> Citation:
        """Build a citation for a result of this tool."""
        return Citation(tool=self.name, query=query, result_count=result_count)


class ProposeTool(ReadTool):
    """Abstract base for `propose`-tier tools (Phase 6, ADR 0025).

    Structurally a tool like any other — same name/description/InputModel/
    OutputModel/`run()` contract — but `tier = ToolTier.propose`, so its schema
    IS shown to the model (a proposal is just data the model may request) while
    it changes no Wazuh state itself: `run()` validates + persists a proposal
    into the approval queue and returns a summary.  Actual execution happens
    only later, after a human approval, inside `wolf_server.gateway.execution`
    — never here, never by the model.
    """

    tier: ClassVar[ToolTier] = ToolTier.propose


def sanitize_organization_id_from_args(
    args: dict[str, Any], _organization_id: uuid.UUID
) -> dict[str, Any]:
    """Strip any organization_id key the model might have included.

    The model never picks organization — if the call somehow has one, we drop it
    silently and rely on the injected OrganizationContext.  See doc 05 §The core
    mechanism.
    """
    if "organization_id" in args:
        return {k: v for k, v in args.items() if k != "organization_id"}
    return args


def strip_explicit_nulls(args: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is exactly ``None``.

    Small / mid-tier models (notably llama3.2) like to emit every parameter
    of a tool schema explicitly, sending ``null`` for the optional ones.
    Pydantic then rejects ``int``-typed fields whose value is ``None`` even
    though those fields have a default — the model never had a chance to
    use the default.  Dropping ``None`` values here lets the Pydantic
    default kick in.

    This is safe because every optional field on a Wolf tool input model
    either has a default or is typed ``T | None``; in both cases dropping
    the ``None`` is equivalent to "not specified".
    """
    return {k: v for k, v in args.items() if v is not None}
