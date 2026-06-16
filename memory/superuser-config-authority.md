---
name: superuser-config-authority
description: "STANDING RULE (operator-stated 2026-06-16): ALL Wolf management + configuration belongs to the Superuser ONLY. Org management → org admins; org-admin/user settings → their respective scope. Every config/management surface is authorized by the role it belongs to. Generalizes wolf-bootstrap-superuser-flow."
metadata:
  node_type: memory
  type: feedback
---

STANDING RULE (operator, 2026-06-16): everything related to **managing and
configuring Wolf itself** — runtime knobs, security gates, system settings, the
component/Wazuh mapping, certs, etc. — belongs to the **Superuser ONLY**. No
other role may touch Wolf-level configuration.

Everything else is scoped to the role it belongs to:
- **Organization management** (create/rename/members/roles within an org) →
  the org's **Admin**.
- **Org-admin-specific settings** → org admins.
- **User-specific settings** → the individual user.
- In general: each setting / config-access surface is authorized by the
  category it belongs to — never broader.

**Why:** Wolf is built for MSSP + multi-org use; conflating "configure Wolf"
with "manage my org" would let a client org change provider-level posture. The
Superuser (the Wolf operator / MSSP provider) owns the platform; org admins own
their org; users own themselves.

**How to apply:** when adding ANY configuration or management surface (API,
CLI, GUI), gate it by the owning role from the start — Wolf-level config →
`require_superuser`; org-level → the org capability (USERS_MANAGE etc.);
user-level → the user themselves. This is the authorization half of
[[web-first-configurability]] (which is the GUI↔CLI↔env *sync* half). The
[[config-settings-system-phase]] (Phase 6.10) builds the Superuser config
surface on this rule. Generalizes [[wolf-bootstrap-superuser-flow]] (Superuser
already owns org/user/role creation + Wazuh mapping) to ALL Wolf config.
