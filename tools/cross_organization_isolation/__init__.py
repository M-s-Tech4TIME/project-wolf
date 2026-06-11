"""Cross-organization isolation test suite — Phase 4 work.

This suite runs negative tests for every read tool, propose tool,
and read endpoint to verify that Organization A cannot access Organization B's data.

Run in CI on every PR: `make test-isolation`
"""
