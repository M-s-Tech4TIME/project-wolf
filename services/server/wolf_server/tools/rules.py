"""Rule definition read tool — Wazuh Server API tier."""

from typing import Any

from pydantic import BaseModel, Field

from wolf_server.tools.base import Citation, ReadTool, ToolExecContext


class GetRuleDefinitionInput(BaseModel):
    rule_id: int = Field(description="Wazuh rule ID, e.g. 5710")


class RuleDefinition(BaseModel):
    id: int
    level: int | None = None
    description: str | None = None
    groups: list[str] = Field(default_factory=list)
    file: str | None = None
    mitre: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class GetRuleDefinitionOutput(BaseModel):
    rule: RuleDefinition
    citation: Citation


class GetRuleDefinitionTool(ReadTool):
    name = "get_rule_definition"
    description = (
        "Full rule definition and metadata for a rule ID — lets the agent "
        "explain why an alert fired."
    )
    InputModel = GetRuleDefinitionInput
    OutputModel = GetRuleDefinitionOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, GetRuleDefinitionInput)
        body = await exec_ctx.server_api.get(
            "/rules",
            params={"rule_ids": args.rule_id},
        )
        items = body.get("data", {}).get("affected_items", []) or []
        if not items:
            return GetRuleDefinitionOutput(
                rule=RuleDefinition(id=args.rule_id),
                citation=self.make_citation(args.model_dump(mode="json"), result_count=0),
            )
        item = items[0]
        mitre = item.get("mitre", {})
        mitre_ids = mitre.get("id") or [] if isinstance(mitre, dict) else []
        if isinstance(mitre_ids, str):
            mitre_ids = [mitre_ids]
        groups = item.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]
        rule = RuleDefinition(
            id=int(item.get("id", args.rule_id)),
            level=item.get("level"),
            description=item.get("description"),
            groups=list(groups),
            file=item.get("filename"),
            mitre=list(mitre_ids),
            raw=item,
        )
        return GetRuleDefinitionOutput(
            rule=rule,
            citation=self.make_citation(args.model_dump(mode="json"), result_count=1),
        )
