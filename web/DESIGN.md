# CompactCode web: design system

The source of truth for every page in `web/`. New pages (for example the live run dashboard)
reuse these tokens, components and motion rules instead of inventing new ones.

Visual direction: dark space, glossy royal indigo 3D tiles on a curved "+" grid, a light cone from the
right, scroll-driven chapters. Inspired by the motion language of puture.co.kr, with our own copy,
icons, colour and layout. The accent is a royal purple-blue, more violet than the reference's blue.
One tile per real agent in `puppeteer_code/agents.py`.

## Tokens (`app/globals.css`, `:root`)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#07061a` | Page background, deep indigo-black (dark only, by design) |
| `--panel` | `rgba(14,12,38,.76)` | Glass panels (bento cells, commands) with `backdrop-filter: blur` |
| `--ink` / `--ink-2` / `--ink-3` | `#f0eefc` / `#bdb9d8` / `#8d88ad` | Text: primary, body, captions (all pass AA on `--bg`) |
| `--line` | `rgba(180,170,240,.17)` | Hairlines and panel borders |
| `--accent` | `#5b3df5` | The single accent: primary buttons, chips, highlight box, tiles. White text on it is 6:1 |
| `--on-accent` | `#ffffff` | Text on the accent |
| `--on-light` | `#0a0822` | Text on light surfaces (the white half of a chip) |
| `--accent-hi` | `#a99bff` | Accent for text and icons on dark |

Scene colours mirror these by hand: `BODY` and the face gradient in `AgentTiles.tsx`, the lights in
`Scene.tsx`, and the `.cone` gradients in `globals.css`.

- Type: Geist (display and body), Geist Mono (chips, chapter badge, commands, formula), via `next/font`.
- Display sizes: `.display-1` hero, `.display-2` centred statements, `.display-3` section titles.
  All scale on `min(vw, vh)`, so a wide, short window never pushes type into the tiles.
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

## App pages (console, runs, benchmarks, docs, research)

- Route group `app/(app)` uses `AppShell`: a sticky bar, the cone at low opacity, and no WebGL, GSAP or
  Lenis. Those stay in `app/(marketing)`. The app is denser than the landing page and has no scroll
  choreography.
- **Panel** (`.panel`): the glass surface for app content. Use `.cell` only on marketing pages.
- `--good` `#7ee2a8` and `--bad` `#ffb4b4` mark state only (server live, tests pass, a diff line, an
  error). They always ship with a word or a `+`/`-` sign, and are never a chart series.
- Every data view has loading (`Skeleton`), empty, error and offline states (`Notice`, `LocalRun`).
- Class names: the landing page owns `.line`, `.step` and `.hero`. Search `globals.css` before adding
  a generic class name, because `.motion-ok .line` once pushed a chart line out of its plot.

## IDE (`app/(ide)/ide`, `components/ide/`)

- Route group `app/(ide)` has no `AppShell`: it is a full-height workspace with a file tree, editor tabs,
  a bottom panel and the chat. State lives in `lib/ide.ts` (`applyEvent` reduces the shared event stream).
- All classes start with `ide`, in the "ide" section of `globals.css`.
- The chat speaks to a non-programmer: plain progress first, agent names only under "Show details".
- Below the IDE's minimum width it shows a notice instead of a squeezed layout. The chat, plan and Undo
  stay usable.
- `/console` still works with `pcode serve` but is out of the nav. The IDE is the app's front door.

## Charts (`components/app/Charts.tsx`)

- One measure, one axis. Two measures means two charts.
- `--accent-hi` marks CompactCode; other methods use `--ink-3`. Identity also comes from the label,
  so there is no categorical palette and no legend.
- Marks are sharp, overriding the usual rounded bar ends, because all UI here has radius 0.
- Bars carry their value in text. Columns and lines get a hover tooltip, and every chart with no
  printed values has a table behind it in `<details>` or in the step timeline.
- Comparisons between methods use the shared-task table, computed from raw rows on the page.

## Motion rules

- Scroll drives everything through GSAP ScrollTrigger + Lenis. No `scroll` listeners, no React state
  for continuous values: triggers write into `lib/sceneState.ts`, the WebGL scene reads it per frame.
- Chapters with choreography are tall sections with a `position: sticky` stage (not GSAP pin). Keep
  them short (240 to 420vh) and `scrub` at 0.2 or less, or a fast scroll leaves text behind its stage.
- The tile story: carousel (hero), ring (Why us), one stack (MCP), grid (How it works), background.
- Text effects (`components/text/effects.tsx`): `Words` (blur to focus), `Highlight` (box wipes in),
  `GlitchType` (block cursor + dither cells). HTML always holds the final, readable state.
- Reduced motion: the `motion-ok` class is added before paint only when motion is allowed. Without it
  sections stack statically, the scene stays inside the hero, and nothing scrubs or loops.
- Never use Motion (framer) alongside GSAP in the same tree.

## Content rules

- All copy lives in `lib/content.ts`. No em or en dashes. Sell first, explain later: lead with the
  reader's problem (running out of tokens), then fewer tokens, then MCP. One line per idea, plain words.
- MCP is the headline feature. Claims about it stay true to `puppeteer_code/mcp_server.py`: any MCP
  client can call it, and the fixer needs a pytest suite. Say the second part once, plainly.
- Numbers must trace to a real-provider run, with task count and scope on the page. Comparisons use
  only the tasks both methods finished. Sim results are never shown. Anything not yet measured stays
  "Pending". Show the unflattering result too (a one-shot prompt wins on small bugs).
