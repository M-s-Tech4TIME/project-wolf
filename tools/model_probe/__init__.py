"""Model probe tool — grades a configured model and outputs its capability descriptor.

Run from the repo root (inside the uv venv):
    uv run python -m tools.model_probe --provider ollama --model llama3.2
    uv run python -m tools.model_probe --provider anthropic --model claude-sonnet-4-6
    uv run python -m tools.model_probe --provider openai --model gpt-4o

Output: a ProbeReport with per-task pass/fail scores and a measured
CapabilityDescriptor (reasoning_tier / recommended_strategy / etc.) that
the orchestrator uses to select the correct agent strategy.
"""
