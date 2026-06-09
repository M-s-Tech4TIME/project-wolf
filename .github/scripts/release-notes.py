"""Generate release notes for a Wolf release.

Reads docs/CHANGELOG.md and either:
  (a) extracts the section that matches the VERSION env var, OR
  (b) falls back to a generic stub with install instructions.

Called from .github/workflows/release.yml. Writes the notes to
/tmp/release-notes.md so the action-gh-release step can pick them
up via body_path.

Avoids the YAML/heredoc indentation tangle that plagued the
previous inline-shell approach (see commit history around
2026-06-09).
"""

from __future__ import annotations

import os
import pathlib
import re

GPG_FINGERPRINT = "D995 2267 30A6 59B3 B86F  CDE7 3772 3B2D E0AB FD65"
REPO = "M-s-Tech4TIME/project-wolf"


def extract_changelog_section(changelog_text: str, version: str) -> str | None:
    """Find the `## ... v{version} ...` section and return its body.

    Returns the body without the heading line, stripped of leading/
    trailing whitespace. Returns None if no matching section found.
    """
    # Match a heading like "## 2026-06-09 — v0.1.0 release" or
    # "## v0.1.0" or "## 0.1.0 release notes". Tolerant of date
    # prefixes, em-dashes, "v" prefix, etc.
    heading_pattern = re.compile(
        rf"^##\s+.*\bv?{re.escape(version)}\b.*$",
        re.MULTILINE,
    )
    match = heading_pattern.search(changelog_text)
    if not match:
        return None
    start = match.end()
    # Find the next "## " heading; everything before it is our section
    next_match = re.search(r"^##\s+", changelog_text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(changelog_text)
    return changelog_text[start:end].strip()


def fallback_notes(version: str) -> str:
    """Generic notes when CHANGELOG.md has no matching section."""
    return f"""Wolf v{version} release.

See [docs/CHANGELOG.md](https://github.com/{REPO}/blob/v{version}/docs/CHANGELOG.md) for the full release history.

## Artifacts

Each .deb is signed with the Wolf maintainers' GPG key (fingerprint `{GPG_FINGERPRINT}`).

Verify before installing:

```bash
gpg --verify wolf-server_{version}_amd64.deb.asc wolf-server_{version}_amd64.deb
```

The Wolf public key is published at [`security/wolf-maintainers.gpg`](https://github.com/{REPO}/blob/main/security/wolf-maintainers.gpg). Import once + verify the fingerprint before trusting:

```bash
gpg --show-keys --with-fingerprint security/wolf-maintainers.gpg
# Expected: {GPG_FINGERPRINT}
gpg --import security/wolf-maintainers.gpg
```

## Install

All-in-one (single host):

```bash
sudo apt install ./wolf_{version}_all.deb \\
                 ./wolf-database_{version}_amd64.deb \\
                 ./wolf-server_{version}_amd64.deb \\
                 ./wolf-dashboard_{version}_amd64.deb
```

Per-component (distributed deployment):

```bash
# On the brain host
sudo apt install ./wolf-database_{version}_amd64.deb ./wolf-server_{version}_amd64.deb
# On the edge host
sudo apt install ./wolf-dashboard_{version}_amd64.deb
```

See [ONBOARDING.md §3.4](https://github.com/{REPO}/blob/v{version}/ONBOARDING.md) for the production-recommended bring-up flow.

## Checksums

`SHA256SUMS` is also attached + signed (`SHA256SUMS.asc`). Verify the whole set end-to-end:

```bash
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS
```
"""


def main() -> None:
    version = os.environ.get("VERSION", "").strip()
    if not version:
        raise SystemExit("FAIL: VERSION env var not set (expected from workflow context)")

    changelog_path = pathlib.Path("docs/CHANGELOG.md")
    if not changelog_path.exists():
        notes = fallback_notes(version)
        print(f"docs/CHANGELOG.md not found — using fallback notes ({len(notes)} chars)")
    else:
        section = extract_changelog_section(changelog_path.read_text(encoding="utf-8"), version)
        if section:
            notes = section
            print(f"Extracted v{version} section from CHANGELOG.md ({len(notes)} chars)")
        else:
            notes = fallback_notes(version)
            print(f"No v{version} section found in CHANGELOG.md — using fallback ({len(notes)} chars)")

    output_path = pathlib.Path("/tmp/release-notes.md")
    output_path.write_text(notes, encoding="utf-8")
    print(f"Wrote release notes to {output_path}")


if __name__ == "__main__":
    main()
