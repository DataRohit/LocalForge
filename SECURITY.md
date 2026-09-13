# Security Policy

## Supported versions

The project has not published a release. Until releases exist, only the current default branch is eligible for security
fixes.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in public issues, discussions, commit messages, or pull requests. Email
`datarohit@outlook.com` or use GitHub private vulnerability reporting when it is enabled for the repository.

Include the affected component, reproduction steps, impact, prerequisite access, relevant logs with secrets removed,
and any proposed mitigation. Do not access data that is not yours, degrade services, or expand testing beyond the
minimum needed to demonstrate the issue.

Maintainers should acknowledge a report within three business days, provide an initial assessment within seven business
days, and share status at least every seven business days until resolution. Timelines may change with severity and
complexity.

## Disclosure

Coordinate public disclosure with the maintainers. After a fix is available, the project should publish the affected
versions, impact, remediation, and credit requested by the reporter without exposing sensitive exploitation details.

## Secrets

Never commit credentials, tokens, private keys, production data, or unredacted sensitive logs. If a secret enters Git
history, revoke or rotate it immediately before removing it from the repository history.
