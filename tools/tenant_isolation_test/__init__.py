"""Cross-tenant isolation test suite — Phase 4 work.

This suite runs negative tests for every read tool, propose tool,
and read endpoint to verify that Tenant A cannot access Tenant B's data.

Run in CI on every PR: `make test-isolation`
"""
