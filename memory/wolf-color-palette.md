---
name: wolf-color-palette
description: Slice 5.0c-c palette — four-colour cool-blue system replacing the earlier wolf-color-palette-outlook.png reference
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

User chose (2026-05-29) a four-colour palette for Slice 5.0c-c, **superseding** the earlier `wolf-color-palette-outlook.png` reference. This palette is the source of truth for Wolf's visual identity going forward.

## The palette

| Name | Hex | RGB | Primary role |
|---|---|---|---|
| **Platinum** | `#e7ecef` | `231 236 239` | Light-mode background / surface; dark-mode foreground |
| **Dusk Blue** | `#274c77` | `39 76 119` | Primary actions, focus, headings, links; dark-mode background |
| **Steel Blue** | `#6096ba` | `96 150 186` | Secondary accents, mid-emphasis surfaces, focus rings |
| **Icy Blue** | `#a3cef1` | `163 206 241` | Highlights, hover states, subtle backgrounds, dark-mode primary |

## Application rules

- **Light mode is the default.** Background is Platinum; primary text near-black with a blue tint (so body copy still hits WCAG AA contrast against Platinum); primary CTAs Dusk Blue; secondary buttons Steel Blue; hover/active backgrounds Icy Blue.
- **Dark mode** flips the relationship — background is a darker shade derived from Dusk Blue (e.g. `#0f1f33`); foreground is Platinum; primary CTAs use Icy Blue so they pop against the dark backdrop.
- **Markdown body, headings, links** all inherit foreground / primary tokens, so updating the CSS variables in `app/globals.css` cascades through `markdown.tsx` without per-component changes.
- **Inline code** uses the muted token (Icy Blue at low opacity in light mode, Steel Blue at low opacity in dark mode).
- **Code blocks** use a slightly more saturated muted surface — keeps them readable but visually distinct from prose.
- **Animations / motion** stay subtle: button hover is a small scale + colour shift; focus rings use Steel Blue (visible against either mode); the existing 200 ms sidebar `transition-[width]` carries over.

## What stays the same

- **Grounding chips keep their semantic colours** — green Verified, amber Uncertain, red Not Verified, muted yellow Non-factual. They are signal-bearing, not decorative, and must remain distinguishable from one another. Their saturation is tuned a notch down so they don't fight the cool-blue surfaces, but never to the point of confusion. See [[verdict-rename-and-four-chips]].
- **Geist** stays the body font.

## Implementation pointer

CSS variables live in [`frontend/app/globals.css`](file:///home/alsechemist/Codespace/project-wolf/frontend/app/globals.css). Tailwind classes (`bg-background`, `text-foreground`, `border-border`, `bg-primary`, `text-primary`, `bg-muted`, `bg-card`, `bg-accent`, `text-destructive`, etc.) resolve through these tokens — most of the existing components require zero code changes once the tokens are right.
