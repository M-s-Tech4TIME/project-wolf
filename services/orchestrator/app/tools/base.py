"""Tool base class, execution context, and citation primitive.

Every read tool subclasses `ReadTool`, declares its input/output Pydantic
models, and implements `run()`.  The runtime is bound to a Wazuh connection
(per request) and the tenant context (per request).

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

from app.guardrails.limits import ResourceLimits
from app.tenancy.context import TenantContext


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

    Built by the dispatcher from the immutable TenantContext + resolved
    Wazuh connection.  Never constructed from model output.
    """

    tenant: TenantContext
    limits: ResourceLimits
    # Wazuh clients are typed as `Any` here so subclasses can opt into the
    # one(s) they need without circular imports.  The dispatcher always
    # populates both.
    opensearch: Any
    server_api: Any


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


def sanitize_tenant_id_from_args(args: dict[str, Any], _tenant_id: uuid.UUID) -> dict[str, Any]:
    """Strip any tenant_id key the model might have included.

    The model never picks tenant — if the call somehow has one, we drop it
    silently and rely on the injected TenantContext.  See doc 05 §The core
    mechanism.
    """
    if "tenant_id" in args:
        return {k: v for k, v in args.items() if k != "tenant_id"}
    return args
