# Puppeteer-Code web: design system

The source of truth for every page in `web/`. New pages (for example the live run dashboard)
reuse these tokens, components and motion rules instead of inventing new ones.

Visual direction: dark space, glossy blue 3D tiles on a curved "+" grid, a blue light cone from the
right, scroll-driven chapters. Inspired by the motion language of puture.co.kr, with our own copy,
icons and layout. One tile per real agent in `puppeteer_code/agents.py`.

## Tokens (`app/globals.css`, `:root`)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#050914` | Page background (dark only, by design) |
| `--panel` | `rgba(9,15,34,.74)` | Glass panels (bento cells, commands) with `backdrop-filter: blur` |
| `--ink` / `--ink-2` / `--ink-3` | `#eef2fb` / `#b4bfd6` / `#8390ad` | Text: primary, body, captions (all pass AA on `--bg`) |
| `--line` | `rgba(160,182,235,.16)` | Hairlines and panel borders |
| `--accent` | `#2a5ff0` | The single accent: primary buttons, chips, highlight box. White text on it is 5.2:1 |
| `--accent-hi` | `#7aa2ff` | Accent for text and icons on dark |

- Type: Geist (display and body), Geist Mono (chips, chapter badge, commands, formula), via `next/font`.
- Display sizes: `.display-1` hero, `.display-2` centred statements, `.display-3` section titles.
  Weight 400 with tight tracking; emphasis uses weight (300 vs 500), never another family.
- Shape: UI is sharp (radius 0): buttons, chips, cells, inputs. Only 3D tiles and the wordmark are rounded.
- z-index scale: cone 0, scene 1, content 2, chapter 40, tile chip 45, nav 50.

## Components

- **Chip** (`.chip`): blue index box + white label box, mono caps. Max two section chips per page.
- **Chapter badge** (`ChapterIndicator`): fixed bottom-left, label decodes on chapter change.
  Sections opt in with `data-chapter="<n>"`; names come from `CHAPTERS` in `lib/content.ts`.
- **Buttons**: `.btn--primary` (accent) and `.btn--ghost` (outlined). One label per intent; the
  GitHub CTA is always "View on GitHub".
- **Nav link**: underline slides in, the `[+]` box turns to an `x`.
- **Tile chip**: DOM label that follows the hovered 3D tile (`03 CODER`).
- **Cell** (`.cell`): glass panel with a pointer-following border spotlight.

## Motion rules

- Scroll drives everything through GSAP ScrollTrigger + Lenis. No `scroll` listeners, no React state
  for continuous values: triggers write into `lib/sceneState.ts`, the WebGL scene reads it per frame.
- Chapters with choreography are tall sections with a `position: sticky` stage (not GSAP pin).
- Text effects (`components/text/effects.tsx`): `Words` (blur to focus), `Highlight` (box wipes in),
  `GlitchType` (block cursor + dither cells). HTML always holds the final, readable state.
- Reduced motion: the `motion-ok` class is added before paint only when motion is allowed. Without it
  sections stack statically, the scene stays inside the hero, and nothing scrubs or loops.
- Never use Motion (framer) alongside GSAP in the same tree.

## Content rules

- All copy lives in `lib/content.ts`. No em or en dashes. No invented numbers: benchmark metrics stay
  "Pending" until a real-provider run exists (sim results are never shown as results).
