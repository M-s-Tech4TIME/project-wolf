"""CLI entry point: python -m tools.model_probe --provider <p> --model <m>

Examples:
    uv run python -m tools.model_probe --provider ollama --model llama3.2
    uv run python -m tools.model_probe --provider anthropic --model claude-sonnet-4-6
    uv run python -m tools.model_probe --provider openai --model gpt-4o
    uv run python -m tools.model_probe --provider openai --model gpt-4o \\
        --base-url http://localhost:8080  # any OpenAI-compatible endpoint
"""

import argparse
import os
import sys
from pathlib import Path

# The probe defers its provider-adapter imports (e.g. `from app.models.ollama
# import OllamaAdapter`) until --provider is parsed.  Those adapters live in
# the orchestrator service package at services/orchestrator/app/.
#
# Subtlety: both services/orchestrator and services/gateway expose a package
# named `app` (each service's internal namespace).  uv's editable installs
# put both on sys.path, and the gateway entry comes first by default — so
# `import app` resolves to services/gateway/app/, which has no `models`.
# We MUST insert the orchestrator dir at sys.path[0] (not just "if absent")
# so it wins the ambiguous name.  This is local to the probe CLI; production
# code never imports across services this way.
_ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / "services" / "orchestrator"
if _ORCHESTRATOR_DIR.is_dir():
    _path_str = str(_ORCHESTRATOR_DIR)
    # Drop any later occurrence so position 0 wins unambiguously.
    sys.path[:] = [p for p in sys.path if p != _path_str]
    sys.path.insert(0, _path_str)

from tools.model_probe.probe import run_probe_sync  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.model_probe",
        description="Grade a configured model and print its capability descriptor.",
    )
    p.add_argument(
        "--provider",
        required=True,
        choices=["anthropic", "openai", "ollama"],
        help="Model provider to probe.",
    )
    p.add_argument(
        "--model",
        required=True,
        help="Model identifier (e.g. claude-sonnet-4-6, gpt-4o, llama3.2).",
    )
    p.add_argument(
        "--base-url",
        default=None,
        help="Override the provider base URL (useful for local or compatible endpoints).",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="API key.  Falls back to ANTHROPIC_API_KEY / OPENAI_API_KEY env vars.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()

    provider_obj = None
    if args.provider == "anthropic":
        from app.models.anthropic import AnthropicAdapter

        api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            sys.stderr.write("Error: ANTHROPIC_API_KEY not set\n")
            return 1
        kwargs: dict[str, object] = {}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        provider_obj = AnthropicAdapter(api_key=api_key, model_id=args.model, **kwargs)

    elif args.provider == "openai":
        from app.models.openai import OpenAIAdapter

        api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
        if not api_key and not args.base_url:
            sys.stderr.write("Error: OPENAI_API_KEY not set\n")
            return 1
        kwargs = {}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        provider_obj = OpenAIAdapter(
            api_key=api_key or "local", model_id=args.model, **kwargs
        )

    elif args.provider == "ollama":
        from app.models.ollama import OllamaAdapter

        kwargs = {}
        if args.base_url:
            kwargs["base_url"] = args.base_url
        provider_obj = OllamaAdapter(model_id=args.model, **kwargs)

    if provider_obj is None:
        sys.stderr.write("Unknown provider\n")
        return 1

    report = run_probe_sync(provider_obj)
    print(report.summary())  # noqa: T201
    return 0 if report.overall_score >= 0.4 else 1


if __name__ == "__main__":
    sys.exit(main())
