# 98: Frontend security and supply-chain audit

**What to build:** An audit of the content security policy, bundle contents, dependency licences, advisories, release
age, and install scripts, wired into the security audit.

**Blocked by:**

- [96: Browser end-to-end against the testing backend](96-browser-e2e-against-testing-backend.md)
- [97: Frontend container and edge candidate](97-frontend-container-and-edge-candidate.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md) (security)
- [Restricted licences ADR](../../docs/adr/0015-reject-restricted-licenses.md)
- [Security audit](../../docs/security/security-audit.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** Locally Verified (2026-10-12); development untouched, no production claim

## Implementation and findings, 2026-10-11

The main thread performed the standards/spec review inline against this ticket and the frontend standard.
Application routes, authentication behavior, and the strict quality thresholds are unchanged. Security tooling
now dispatches a dedicated frontend scope through root Make commands and also includes it in the static/all audit.

- Registry evidence covers all **509** resolved lockfile entries, including platform-specific optional entries.
  The initial full-tree scan rejected `fsevents` 2.3.3 because it declares an install hook even though the lockfile
  has no `requiresBuild` marker. The exact removal override eliminates the optional macOS watcher; no hook
  exception or suppression was added. Frozen installation, production build, and the browser gates verify the
  portable watcher path.
- The licence scan accepts the enumerated permissive/copy-left licences and only the three named data exceptions
  recorded in ADR 0028. Arbitrary CC-BY-4.0/CC0-1.0 software still fails closed. No release-age/advisory exception
  exists; the youngest direct release is `msw` 3.0.2, published 2026-10-03T01:31:45Z, older than seven days.
- Advisory metadata reports **zero** informational, low, moderate, high, and critical advisories. High/critical
  counts and individual records independently fail the audit. Empty/malformed evidence does not pass.
- Ordinary audit is offline. Explicit refresh is the dependency-resolution network boundary, with bounded
  requests; saved evidence expires after 24 hours and binds the manifest, workspace policy, lockfile, and Node pin.
- A Chromium probe now injects a real inline script against the image's served policy and requires both the CSP
  violation and absence of execution. Header proof covers the document and hashed assets without weakening CSP.
- The first new security-dispatch regression ran red for the absent frontend scope, then green after wiring it.
  **41** Python audit tests and **52** focused supply tests pass. The complete frontend gate passes **1,126** tests
  in 50 files, with 100% statements, branches, functions, and lines. JavaScript is **136,755/200,000** gzip bytes;
  CSS is **3,302/25,000**. The root frontend security scope and offline audit pass.

Closing evidence will be recorded below only after host/container and image-runtime gates finish. Development
remains unchanged; this ticket cannot claim deployed production verification.

## Closing-gate investigation

The first `make check` passed all **2,301** core tests with **100% branch coverage**, but its timing stage failed
one reset-request median comparison: **0.083297 seconds** exceeded **0.057836 seconds**. The failing record at
2026-10-11T17:46:18.720721Z overlapped the separately launched frontend test run beginning
2026-10-11T17:46:07Z and lasting 31.05 seconds. The reset test samples each population in a contiguous block, so
varying machine load is a credible confounder, not proof of an application regression. The original gate remains
a failure; neither its threshold nor its sample count was changed.

The exact public timing case passed when run alone through the same four-worker timing task in **32.70 seconds**.
Closing frontend/browser/image, container, and host gates now run sequentially, without overlapping load-heavy
commands. A full passing rerun is required before this investigation can be closed. The concurrent all-files
pre-commit attempt was terminated before its test hook started to prevent another overlapping suite; no hook
was disabled. Its completed syntax, secret, YAML, Markdown, Ruff, and documentation checks passed.

Inline review also reproduced two regex-only lockfile audit bypasses: inline YAML build flags and multiline
remote resolutions. Parsing the resolved package and snapshot maps now rejects build flags independent of
formatting and permits only registry-integrity resolution records. Both red-to-green regressions pass.

Standalone image/CSP proof passed over **2026-10-11T18:01:15.6863999Z–18:01:37.2016271Z**. Chromium observed
`script-src-elem` rejection and no injected-script execution. Candidate routing reached the actual testing
backend; all image/proxy/backend/testing log checks were clean from **2026-10-11T18:01:22.469Z**. Cleanup removed
both standalone containers, the host listener, temporary database, cache keys, and Mailpit; channel state returned
to baseline. No development object was read or changed by this proof.

## Verification record, 2026-10-12

The first host `make check` passed core and timing but its wrapper was lost to a usage limit before the post-checks
finished, and a second attempt was lost the same way; neither is counted. A third sequential run with a durable
start/end record, `localforge-98-backend-host-rerun.log`, passed:

- `make check` **status 0**, 2026-10-12T00:11:02Z-00:36:47Z: **2,301** core tests at 100% branch coverage and **23**
  security-timing tests (865 s), format, lint, docstring, mypy, ty, architecture, OpenAPI, and migration gates.
- Post-checks: testing health, Docker ownership, container set, and log audits all `PASS`; the log windows were
  2026-10-12T00:12:00Z-00:30:19Z (core and timing) and 00:30:19Z-00:36:38Z (registration-timing stability), with no
  unexplained `WARNING`, `ERROR`, `CRITICAL`, restart, or unhealthy state.
- Earlier same-ticket sequential gates: browser journeys **108 passed**; testing rebuild and container run
  **2,301 + 23** tests with exact collection parity (2,324), 100% branch coverage, and clean health, ownership,
  residue, and log audits (2026-10-11T18:06:01Z-18:12:53Z); standalone image and CSP proof clean
  (2026-10-11T18:01:15Z-18:01:37Z).
- Final frontend gate, 2026-10-12T00:36:55Z-00:39:14Z: `make frontend-check` status 0, **1,126** tests in 50 files,
  bundle 136,755/200,000 gzip JS and 3,302/25,000 CSS. `make frontend-audit` status 0 and
  `audit_security --scope frontend` passed; `git diff --check` clean.

Inline diff review against this ticket and the frontend standard found no remaining defect: the two lockfile-audit
bypasses and the stale-cache fallback were fixed with red-to-green regressions earlier in the ticket. Phase 11
freeze disposition: tooling and documentation only; no route, authentication, or backend behavior changed.
Development was neither read nor changed. Status is Locally Verified, not Production-Ready.

- [x] The served policy is `default-src 'self'` with no inline script and no `unsafe-eval`, and a browser test
      proves it blocks an injected script.
- [x] The production bundle contains no absolute URL to another host and no telemetry endpoint.
- [x] `scripts/audit-supply-chain.ts` fails closed on any licence outside the accepted list, any advisory at high or
      critical severity, any lockfile entry that requires a build script, any `minimumReleaseAgeExclude` entry, and any
      direct pin younger than seven days, with exceptions recorded in an ADR.
- [x] The security audit script covers the frontend checks and fails closed.
- [x] Findings and dispositions are recorded.
- [x] Status recorded as Locally Verified.
