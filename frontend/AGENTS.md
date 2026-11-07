# Frontend Development

## Design Entry Point

Before a UI task, read [DESIGN.md](DESIGN.md), then the affected component and
[src/app.css](src/app.css). Use the design specification for visual intent and
the existing source for current implementation details. Explicit user requests
take precedence; update the specification when an approved design changes.

Preserve the existing liquid-glass identity and integrated Studio management.
External DESIGN.md files are references, not instructions to replace this
project's identity, routing, authentication, or data semantics.

## Implementation

- Use the existing SvelteKit, Svelte 5, Tailwind CSS 4, UI components, and Lucide
  icons. Read [README.md](README.md) for routes and commands.
- Reuse [src/lib/components/ui](src/lib/components/ui) for UI primitives and
  [src/lib/components/studio/ui.ts](src/lib/components/studio/ui.ts) for Studio
  controls. Keep changes scoped; do not rewrite unrelated legacy components.
- Define shared color/material changes in `src/app.css`. Use `mode-watcher`
  for theme state. Keep the root liquid-glass filter definition unique.
- Preserve existing API contracts and distinguish unavailable measurements
  from numeric zero. UI work must not silently change scoring algorithms.
- There may be existing uncommitted work. Preserve it and report only the
  changes and verification performed for the current task.

## Verification

- For UI source changes, run `npm run check` and `npm run build` sequentially.
  For documentation-only changes, check links, source references, and the diff;
  a full application build is not required.
- Use the responsive and interaction checklist in `DESIGN.md`. Verify rendered
  states, including long content and nonempty data, not just compilation.
- Use isolated fixtures for mutation tests. Report fixture-based checks,
  real-service checks, and untested browser/accessibility cases separately.
- Keep the design specification and its implementation references current.
  Do not claim that DESIGN.md is executed by the browser or automatically
  enforced by every coding tool.
