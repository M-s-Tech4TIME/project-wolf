"""Write a single value into the configured secrets backend.

Reads the value from stdin so the secret never appears on the command
line or in shell history.  Useful for stashing model-provider API keys
(Anthropic, OpenAI, OpenRouter, DeepSeek direct, etc.) under a chosen
key name that the orchestrator then references via
DEFAULT_MODEL_API_KEY_REF.

Usage:
    echo -n "$KEY" | uv run python -m wolf_server.management.set_secret \\
        --key model.openrouter.api_key

  # Or, if you have the key in a file:
    uv run python -m wolf_server.management.set_secret \\
        --key model.openrouter.api_key < ./path/to/key.txt

The backend is whatever SECRETS_BACKEND is configured to in the
environment / Settings.  For the dev encrypted-file backend, the value
is written into .local/secrets.enc (Fernet-encrypted with
SECRETS_FILE_KEY).

The CLI never echoes the secret it just wrote.  It prints only the key
name, the backend type, and a length-only confirmation.
"""

import argparse
import asyncio
import sys

from wolf_server.config import get_settings
from wolf_server.secrets_factory import get_secrets_backend


async def set_secret(key: str, value: str) -> None:
    settings = get_settings()
    backend = get_secrets_backend(settings)
    await backend.set(key, value)
    sys.stdout.write(
        f"✓ stored {len(value)}-byte value under key {key!r} "
        f"in {settings.secrets_backend!r} backend\n"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--key",
        required=True,
        help="The secret key (e.g. 'model.openrouter.api_key').",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # Read the value from stdin so it never appears on the command line
    # and never lands in shell history.
    if sys.stdin.isatty():
        sys.stderr.write(
            "Error: secret value must be supplied on stdin.\n"
            "Usage: echo -n \"$KEY\" | uv run python -m wolf_server.management.set_secret "
            "--key <name>\n"
        )
        return 2
    value = sys.stdin.read().rstrip("\n")
    if not value:
        sys.stderr.write("Error: empty value on stdin; refusing to store.\n")
        return 2

    asyncio.run(set_secret(args.key, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
