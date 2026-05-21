"""Wazuh integration layer — tenant-scoped clients for OpenSearch and Server API.

Rule from doc 05: the query-construction layer **forces** the tenant filter as a
mandatory clause.  There is no code path in this package that produces a query
without it.  Every public entry point requires a TenantContext.
"""
