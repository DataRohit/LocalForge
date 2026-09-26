---
status: accepted
date: 2026-09-13
---

# SOPS and age for secrets, one `.env` per environment

Secrets are generated locally by a script, held in one `.env` file per environment that Compose loads through
`env_file:`, and committed only in age-encrypted form via SOPS v3.13.3 (2026-07-23, MPL-2.0) with age v1.3.2
(2026-08-29, BSD-3-Clause). Inside Django the values are read through `django-environ` 0.14.0, which declares Django
6.0 and Python 3.14.

SOPS moved from Mozilla to the CNCF `getsops` organization and releases every six to eight weeks. `age` is
deliberately low-churn — three releases since December 2024 — which is a design goal for a cryptographic primitive,
not a staleness signal.

## The model

| Artifact | Committed | Contents |
| --- | --- | --- |
| `.env.development`, `.env.testing`, `.env.testing.host` | **no** | real values, generated locally |
| `.env.development.sops`, `.env.testing.sops` | yes | the same files, age-encrypted |
| `.env.example` | yes | every variable name, placeholder values only |

A fresh clone can therefore reproduce a working environment without a secret ever entering Git history.

One Compose detail makes this safe rather than merely tidy: **`environment:` overrides `env_file:`**. Any literal
left inline in a Compose file silently wins over the generated file, which is why the rule in
[../platform/conventions.md](../platform/conventions.md) is that no value is hardcoded inline at all.

## Considered options

**HashiCorp Vault 2.1.0** — **BUSL-1.1** since v1.15.0 with **IBM** now named as licensor, so not open source. Even
setting the licence aside it is a server that must be unsealed, adding a bootstrap problem to a platform whose point
is to start from `docker compose up`.

**OpenBao v2.6.2** — the clean MPL-2.0 fork, and the right answer if a secrets *server* were needed. Rejected for
shape, not quality: a local development platform needs no dynamic secrets, leasing, or revocation, and running a
server to hold a handful of passwords is not justified.

**Infisical v0.165.10** — open-core, MIT except a proprietary `ee/` directory. Same shape objection, plus a licensing
boundary to police.

**direnv v2.37.1** — no release since 2025-07-20, no commits in thirteen weeks, 468 open issues. Compose's
`env_file` does the same job with no extra tool. See [0015](./0015-reject-restricted-licenses.md).
