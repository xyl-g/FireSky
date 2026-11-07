---
version: "1.0"
name: Xiao-Yang-Liquid-Glass
description: "A personal photography and writing site, an atmospheric observation workspace, and an integrated Studio console sharing a restrained liquid-glass identity."
source_of_truth: src/app.css
theme_controller: mode-watcher
colors:
  canvas: "var(--background)"
  ink: "var(--foreground)"
  muted_ink: "var(--muted-foreground)"
  primary: "var(--primary)"
  on_primary: "var(--primary-foreground)"
  surface: "var(--card)"
  border: "var(--border)"
  glass: "var(--glass-surface)"
  glass_strong: "var(--glass-strong)"
  glass_edge: "var(--glass-border)"
  error: "var(--destructive)"
---

# Xiao Yang Design System

This is the canonical design contract for this frontend repository. Metadata
and prose guide development; neither is a runtime configuration or a validated
Stitch interchange schema. The document follows the DESIGN.md approach from
[Awesome DESIGN.md](https://github.com/voltagent/awesome-design-md), with original
project-specific rules grounded in the current source.

## 1. Visual Theme & Atmosphere

Retain the existing site identity: photography and writing, compact glass
navigation, translucent surfaces with fine highlights, readable light and dark
modes. Refine alignment, density, contrast, and interactions incrementally.

| Surface | Primary user need | Visual treatment |
| --- | --- | --- |
| Showcase | Inspect photos, read logs, discover software | Media and readable content lead; use restrained chrome and generous reading space |
| Observation | Select a point, compare times, understand forecast/nowcast results | Dense left controls/right map on desktop; distinguish result, drivers, and diagnostics |
| Studio | Manage content, accounts, jobs, data, and schedules | Quiet operational layout with consistent navigation and compact, scannable lists |

The homepage must retain its established composition. Do not add a large hero
image, oversized promotional heading, or full-screen marketing section as part
of routine polish. Sunset Auto management belongs in `/studio/console`; legacy
`/admin/*` routes are compatibility redirects. Overview and system health belong
in the same overview, not two independent dashboard experiences.

## 2. Color Palette & Roles

Use semantic Tailwind classes such as `bg-background`, `text-foreground`,
`text-muted-foreground`, `border-border`, and `bg-primary`. Chart/SVG code can
reference CSS variables directly. Do not paste these snapshots into components.

The following values were checked against [src/app.css](src/app.css) on
2026-09-09. The CSS remains the implementation source of truth; update this
table with any intentional token change.

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--background` | `oklch(0.985 0.005 75)` | `oklch(0.138 0.018 265)` | Page canvas |
| `--foreground` | `oklch(0.18 0.012 260)` | `oklch(0.93 0.008 75)` | Main text |
| `--muted-foreground` | `oklch(0.50 0.012 260)` | `oklch(0.72 0.012 260)` | Secondary text |
| `--card` | `oklch(0.995 0.002 75)` | `oklch(0.185 0.015 262)` | Solid surface/fallback |
| `--primary` | `oklch(0.60 0.18 42)` | `oklch(0.76 0.14 62)` | Observation actions and focus emphasis |
| `--border` | `oklch(0.92 0.01 75)` | `oklch(0.255 0.012 262)` | Structural boundaries |
| `--glass-surface` | `oklch(0.995 0.002 75 / 0.66)` | `oklch(0.23 0.012 262 / 0.64)` | Translucent panel |
| `--glass-strong` | `oklch(0.995 0.002 75 / 0.84)` | `oklch(0.24 0.012 262 / 0.86)` | Emphasized glass |
| `--chart-quality` | `#c2410c` | `#fb923c` | Quality series |
| `--chart-coverage` | `#047857` | `#34d399` | Cloud coverage series |
| `--chart-illumination` | `#a16207` | `#fbbf24` | Illumination series |
| `--chart-transmission` | `#0369a1` | `#38bdf8` | Transmission series |

Keep local brand accents where already established: Showcase uses blue accents,
Studio's existing `primaryBtn` uses indigo, and atmospheric charts use multiple
semantic hues. Reuse the local helper instead of globally recoloring these
surfaces to match a borrowed brand.

Observation and Studio share Showcase's canvas and existing soft ambient
background on `body::before`, in both light and dark modes. The table above
applies to all three surfaces, including portaled menus. Keep `.glass-workspace`
transparent so the shared backdrop remains visible through the working rails
and panels. Do not add a separate gray workspace theme or a second orange/teal
background wash. Preserve local action/status accents, glass opacity levels
and the working layouts.

Status meaning must remain stable: primary for active computation, emerald for
success, amber for waiting/attention, and destructive/rose for failures. Include
a text or icon signal so color is not the only distinction. Choose foreground
and background as a pair in both modes; light badges need dark text. A status
color alone is not evidence of a 4.5:1 contrast ratio on translucent surfaces.

## 3. Typography Rules

| Usage | Existing family/helper | Size guidance |
| --- | --- | --- |
| Chinese UI/body | PingFang SC, Microsoft YaHei, system sans-serif | 14-16px; preserve readable line height |
| Display headings | `.font-display`, Outfit with Chinese/system fallbacks | 24-36px for page titles, according to the existing surface |
| Panel headings | Inherited UI font | 14-18px; never hero-scale inside a compact panel |
| Secondary labels | Inherited UI font | 12-13px; avoid lower-opacity text for important information |
| Measurements/time | `.font-mono-data`, JetBrains Mono with monospace fallbacks | 12-14px, tabular numerals |
| Main quality score | `.font-mono-data` in ScoreCard | 36px; keep the API's scale and precision |

For new or revised text, use zero letter spacing and breakpoint-based fixed
sizes rather than viewport-width font sizing. Do not import a reference brand's
proprietary font. Use wrapping and `min-width: 0` before reducing text size.
Keep units visible; truncation must not hide a value, status, or essential action.

## 4. Component Stylings & States

| Need | Reuse | Behavior |
| --- | --- | --- |
| General controls | [UI primitives](src/lib/components/ui) | Reuse Button, Input, menus, dialogs, sheets, badges, and their focus/disabled behavior |
| Studio controls | [ui.ts](src/lib/components/studio/ui.ts) | Reuse `inputCls`, `primaryBtn`, `ghostBtn`, `iconBtn`, `cardCls`, and `notify` |
| Theme controls | [ThemeToggle](src/lib/components/site/ThemeToggle.svelte) and `mode-watcher` | One shared state across Showcase, observation, and Studio |
| Forecast summary | [ScoreCard](src/lib/components/forecast/ScoreCard.svelte) | Main score, measured drivers, best time; diagnostics below |
| Charts | [TrendChart](src/lib/components/forecast/TrendChart.svelte) | Reuse series tokens; distinguish lines with labels/dashes as well as color |
| Maps | [MapView](src/lib/components/map/MapView.svelte) / [OutdoorForecastMap](src/lib/components/outdoor/OutdoorForecastMap.svelte) | Preserve Leaflet, resize handling, viewport bounds, and accessible controls |
| Gallery | [PhotoGallery](src/lib/components/site/PhotoGallery.svelte) | Stable media aspect ratio, loading skeleton, keyboard entry to lightbox |
| Management lists | [UsersSection](src/lib/components/studio/UsersSection.svelte) / [TasksSection](src/lib/components/studio/operations/TasksSection.svelte) | Retain selection, actions, and records when rows adapt to narrow screens |

Use Lucide icons for tool buttons, segmented controls for modes, checkboxes or
switches for binary settings, and numeric inputs/sliders for measured values.
Icon-only buttons need an accessible name and a tooltip/title. A command such as
creating an item may use icon plus text. Preserve hover, pressed, selected,
focus-visible, disabled, loading, empty, error, and stale-data states where
applicable. Tooltips must not be the only way to identify a control on touch.

Keep cards for repeated items, dialogs, or genuinely framed tools. Use unframed
layouts for sections. Follow existing local radii: the project currently uses
12px base and 16px glass frames as well as 6-8px compact controls; do not impose
a new global radius. Avoid additional nested glass cards.

## 5. Layout Principles

- Use the existing 4px spacing rhythm: 4, 8, 12, 16, 24, 32, and 40px. Prefer
  12-24px gaps in work surfaces, with larger spacing reserved for reading.
- Showcase content uses its existing constrained width. Photo grids are
  one/two/three columns; media reserves a 4:3 area before loading. Software
  screenshots use a stable 16:9 area with `object-contain` to preserve detail.
- Article and software details use a 240px information rail, up to 760px reading
  content, and up to 40px gap on desktop. Article navigation becomes a bounded
  block above the article on narrow screens.
- Forecast/nowcast use the shared `.observation-workbench`, `.workbench-rail`,
  `.workbench-main`, `.workbench-map`, and `.workbench-results` layout. Desktop
  controls use a 340px rail. Give each pane one scroll owner and `min-height: 0`.
- Separate main result, contribution factors, and expandable diagnostics. Show
  the best observation time in outdoor results without hiding confidence or
  missing-data states. Never invent a score or replace unavailable data with 0.
- Studio is one console. Reuse its navigation and surface rhythm; avoid a second
  page shell, duplicate page-level headings, or a standalone system dashboard.

## 6. Depth & Liquid Glass

Reuse `.glass`, `.glass-strong`, `.glass-panel`, `.glass-panel-strong`,
`.glass-rail`, `.glass-control-well`, and `.glass-control-active` according to
the surrounding component. The `glass` and `glass-panel` families have existing
differences in `src/app.css`; do not assume they are interchangeable aliases.

Glass is composed of a translucent surface, subtle border, inset highlight,
blur, and restrained shadow. Stronger glass supports a foreground control or
summary; a rail is visually quieter. Keep foreground text crisp and avoid blur
or glow on text itself.

| Material | Apply to | Optical treatment |
| --- | --- | --- |
| `.glass` | Existing Showcase frames and media chrome | Clearer gradient fill with directional edge highlights |
| `.glass-panel` | Studio records, overview and observation panels | More opaque semantic fill for dense data; same edge/reflection recipe |
| `.glass-strong`, `.glass-panel-strong` | Menus and foreground surfaces | Higher opacity and raised shadow for reliable separation |
| `.glass-nav` modifier | Showcase, observation and Studio headers | Strong fill, restrained shadow, desktop Chromium refraction |
| `.glass-rail` | Working/sidebar rail | Subtle fill and a separator rather than a floating shadow |
| `.glass-control-well`, `.glass-input` | Embedded controls/forms | Inset surface with no additional backdrop filter |
| `.glass-control-active` | Selected navigation/segments | Bright inset rim and contact shadow; no layout shift |

All materials share `--glass-specular`, `--glass-highlight`, `--glass-border`
and the three shadow tokens in CSS. `.glass-panel` families keep their existing
caller-owned border width and radius. Do not add duplicate ring outlines.
Reflections belong to background layers or masked edges, never across text.
Desktop blur is 18px for panels and 24px for strong surfaces; at widths up to
768px it becomes 10px/14px with contact shadows and no SVG refraction. Saturation
is 135% in light mode and 120% in dark mode. Do not use fixed filter values in
browser-specific selectors that bypass these responsive tokens.

The filter `#liquid-glass-lens` is defined once in
[the root layout](src/routes/+layout.svelte). Chromium enhancement is enabled
on the root HTML element by [src/app.html](src/app.html). Never duplicate the
filter or hardcode `chromium-glass` on a component; other browsers need the CSS
blur/fallback path. Refraction is opt-in through `.glass-nav`, with displacement
scale 8; cards and long rails only use CSS blur. Reduced motion removes the SVG
filter and retains the existing 12px blur fallback; increased contrast and
reduced transparency use opaque surfaces. Keep
these overrides and the no-backdrop-filter fallback. Do not add
`contain: paint layout` or `translateZ(0)` indiscriminately;
they can change clipping, stacking, and sticky behavior.

## 7. Do's and Don'ts

Do preserve the current homepage direction, real media, liquid glass, shared
theme state, integrated management, and existing API contracts. Make controls
and important information easier to scan before adding decoration.

Do not copy a brand template wholesale, reintroduce a homepage hero image,
replace the framework, add a parallel theme engine, or rebuild administration
as a separate application. Do not add decorative orbs, excessive gradients,
particle score halos, or new hardcoded dark-mode overrides for Studio text.

Reference DESIGN.md files describe third-party surfaces and may include gaps,
stale observations, or instructions unsuitable here. Treat them as design
research. An explicit user-requested visual change can override this contract;
record the new decision and keep its implementation and documentation aligned.

## 8. Responsive Behavior & Accessibility

| Width | Expected behavior |
| --- | --- |
| Below 640px | Single-column gallery and compact reading/forms; retain complete action labels and data |
| 640-1023px | Gallery may use two columns; observation uses map/parameters/results segments; Studio schedules and accounts use narrow-screen rows/cards |
| 1024px and above | Observation uses the 340px rail; reading rails become sticky; Studio may use tables |
| Wide desktop | Constrain prose and content; allow the working map to use available space |

Breakpoints are defaults, not evidence that content fits. At 1024px a desktop
Studio sidebar reduces available table width; keep actions reachable through
container-aware adaptation or an explicit horizontal table scroll region.

Touch controls need an effective 44px target, including icon buttons and
checkbox labels. Measure the rendered target: utility or scoped CSS can override
a global minimum. Do not allow expanded touch areas to overlap adjacent actions.

Verify map controls against toolbars, sticky navigation, timeline controls, and
bottom panels. A map must size to its available container on short screens.
Preserve map instances and selected values when switching mobile views.

Use semantic headings, explicit labels, visible keyboard focus, and keyboard
operation for interactive media. Important text should meet WCAG AA contrast
against its composited background. Respect `prefers-reduced-motion`; loading
must still have a visible status when animation is reduced. Skeletons and aspect
ratios reduce layout movement but do not prove CLS = 0.

## 9. Agent Prompt Guide

Start a scoped UI change with this prompt:

> Read AGENTS.md and DESIGN.md, then inspect the affected source. Improve the
> requested component while preserving the liquid-glass identity, local accents,
> integrated Studio navigation, theme synchronization, and data semantics. Reuse
> current primitives. Verify the relevant loading, empty, error, and populated
> states in light/dark and narrow/wide layouts. Report exactly what was tested.

For a reference-inspired change:

> Use the linked Linear DESIGN.md only to study information density and grouping.
> Apply those ideas to the requested Studio section using this project's
> DESIGN.md and tokens. Preserve the existing color, typography, and glass system.

For review:

> Review this diff against DESIGN.md. Lead with actionable regressions and source
> locations. Check duplicate page shells, theme/token drift, clipped actions,
> touch/keyboard access, stale-data presentation, and whether each changed control
> retains its behavior. Separate observed results from unverified expectations.

## 10. Review & Verification

For UI implementation changes, run `npm run check`, then `npm run build` from
this repository, and inspect the diff. These commands verify code/build behavior,
not visual conformance. Documentation-only changes need reference/link checks.

For affected views, check the following with rendered pages and interactions:

- Widths 390, 768, 1024, and 1440px; include 390x667 and 1440x720 for short screens.
- Light, dark, and system preference; retain theme selection across routes/reload.
- Loading, empty, unavailable/error, stale-data, and realistic nonempty content.
- Long names, timestamps, numeric values, JSON, and every key list action.
- Both document overflow and clipped child rectangles; inspect screenshots.
- Keyboard focus/activation, coarse-pointer targets, and reduced motion.
- Map/image loading and map resizing after view changes; no obstructed controls.
- A single glass filter and correct browser fallback; no unnecessary shared CSS
  changes outside the affected surface.

Use isolated fixtures for operations such as running tasks, changing user roles,
or deleting data. State whether checks used fixtures or a real backend. When
intercepting requests in Vite tests, match `/api/v1/` rather than broad `/api/`
patterns that also capture frontend module paths.

## 11. Known Gaps & Maintenance

This is an incremental design contract, not a claim that all existing components
already conform. Legacy local colors, small targets, and duplicate headings may
remain; address them in the affected scope rather than performing blanket
rewrites. Complete Safari/device checks, contrast measurements, and CLS profiling
must be reported from current evidence, not inferred from CSS or earlier runs.

When a shared token or pattern changes, update its source, this document, and the
affected component examples in the same change. Use actual pages and shared
components for visual verification; do not create an unrelated sample UI that
quietly becomes a second source of design tokens.

See [integration notes](docs/design-md-integration.md) for the upstream snapshot,
reference selection, and the distinction between a design document and runtime
theme support.
