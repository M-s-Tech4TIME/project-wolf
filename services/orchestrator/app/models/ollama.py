"""Ollama model adapter — Phase 1 stub.

Implementing the full ModelProvider interface for Ollama (local models)
is Phase 1 work.  This file exists to prove the "no paid dependency
required" promise: Wolf always has a local-model code path.

Phase 1 will implement:
  - ModelProvider interface
  - Capability descriptor grading
  - Structured-JSON-output fallback for models without native tool-calling
  - Full async streaming
"""

# Phase 1: implement OllamaAdapter(ModelProvider)
