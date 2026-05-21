"""Management CLIs — operator scripts for one-shot administrative tasks.

These commands are invoked outside the running orchestrator service.  They
read the same configuration (DATABASE_URL, SECRETS_FILE_PATH, etc.) so they
operate on the deployed state, not a separate config.

Examples:
  python -m app.management.bootstrap_tenant ...
  python -m app.management.smoke_wazuh ...
"""
