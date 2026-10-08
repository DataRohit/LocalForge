---
status: accepted
date: 2026-10-08
---

# React and Tailwind CSS for the frontend

The frontend is built with React for components, state, and client-side behavior, and Tailwind CSS as the primary
styling system, compiled into local bundled styles with no runtime CDN. The user mandated both on 2026-10-08. Other
UI or CSS frameworks are not proposed or substituted.

## Boundaries

- No server-side rendering runtime and no backend-for-frontend; the frontend is a static single-page application.
- No hosted runtime dependency, external authentication provider, telemetry, or font and asset CDN.
- Frontend validation improves usability only; the backend stays authoritative for validation, authorization, and
  state.
- Navigation is driven by capabilities the backend grants, never by client-side assumptions.

## Considered options

Vue, Svelte, Sass, CSS-in-JS, and a framework with its own server runtime (Next.js, Remix framework mode) were
excluded by the mandate or by the no-SSR and no-BFF boundary.

## Consequences

- The supporting toolchain is recorded in [0028](./0028-frontend-toolchain.md); the working rules are in
  [the frontend standard](../platform/frontend-standard.md).
- Both choices carry lock-in; replacing either is a new ADR with dated primary-source evidence, as for any tool.
