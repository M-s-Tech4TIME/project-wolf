# credentials.example/ — placeholder credential templates

The real `credentials/` directory is **gitignored** (per-machine operator
drop, never committed — the repo is public). This directory is its tracked
template: one file per credential set the project needs, every value
replaced with a `CHANGE_ME` placeholder and annotated with where the real
value comes from.

**Setup:** `cp -r credentials.example credentials`, then fill in the real
values in `credentials/` (which git ignores). Where each file's values are
*used*:

| File | Consumed by |
|---|---|
| `wazuh-credentials.txt` | `bootstrap_organization` CLI (per-org Wazuh wiring); Superuser component-mapping GUI |
| `postgresql-credentials.txt` | `DATABASE_URL` in `.env` (printed by `make wolf-database-init`) |
| `openrouter-credentials.txt` | secrets backend ref `model.openrouter.api_key` (only if using a hosted model) |
| `wolf-credentials.txt` | Wolf web-UI logins (bootstrap Superuser + org admins/users) |

See `ONBOARDING.md` §3 for the full setup sequence and
`docs/HANDOVER.md` §7 for context.
