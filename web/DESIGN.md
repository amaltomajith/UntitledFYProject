# CompactCode web: design system

The source of truth for every page in `web/`. New pages (for example the live run dashboard)
reuse these tokens, components and motion rules instead of inventing new ones.

Visual direction: dark space, glossy royal indigo 3D tiles on a curved "+" grid, a light cone from the
right, scroll-driven chapters. Inspired by the motion language of puture.co.kr, with our own copy,
icons, colour and layout. The accent is a royal purple-blue, more violet than the reference's blue.
One tile per real agent in `puppeteer_code/agents.py`.

## Memory Graph (`components/ide/MemoryGraph.tsx`, `lib/graph.ts`)

`d3-force` on a `<canvas>`, because SVG does not hold 300 live nodes. Kinds differ by shape, label and colour
(`--graph-*` tokens, never colour alone): session hexagon, episode diamond, file square, symbol circle. A failed episode
uses `--graph-bad`, and the inspector states its result in words. The list beside the canvas is the keyboard and screen
reader route to every node, the same rule as a chart with a table behind it. With reduced motion the layout settles first
and draws one frame. "About N tokens saved" stays worded as an estimate, with its method in the tooltip.

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

- Route group `app/(ide)` has no `AppShell`: it is a full-height workbench with an activity bar, side views,
  editor tabs, a bottom panel and the agent chat. State lives in `lib/ide.ts` (`applyEvent` reduces the
  shared event stream). The layout is nested `react-resizable-panels` groups, saved in localStorage.
- All classes start with `ide`, in the "ide" section of `globals.css`.
- **The IDE is near-black with slight violet, matching the landing page, by the owner's decision.** `.ide` and
  `.ide-setup` redefine the neutrals (`--bg`, `--ink*`, `--line`, `--well`), so every other page is unchanged.
  The accent tokens come from `:root`. Violet is sparing: primary buttons, focus ring, activity marker, active
  tab line, changed-file and unsaved marks, inline code, warnings (which keep their glyph and words).
  `--select` is the selection tint. Syntax colours are `--syn-*`. Never write a colour in an `ide` rule; add a
  token. ANSI colours in `terminal/store.ts` are the exception: programs give them a meaning, so yellow stays yellow.
- Type: Geist for the workbench and chat, as on the landing page. Geist Mono only for the editor, terminals and code.
- Shape: sharp like the rest of the site. The one exception is the agent panel: the prompt card and message
  bubbles use `--r-chat` (10px).
- Icons: codicons (`Codicon.tsx`) for the workbench. File and folder logos are a Material Icon Theme subset in
  `public/file-icons/`, with the name map in `lib/fileIcons.json`. `web/scripts/file-icons.mjs` regenerates both, and
  the result is committed, so the Next build has no extra step. Unknown files get the generic icon.
- Activity bar: only views that work. Explorer, Search, Source Control (read-only) and Testing. Do not add a
  button that does nothing, and the same goes for bottom panel tabs and the prompt card.
- Terminals are real shells (`components/ide/terminal/`): `store.ts` holds each xterm outside React, so one survives
  moves between the panel and the editor; a slot adopts its host `<div>`. From two terminals up a rail appears on the
  right. Drags: every sash, rail rows to reorder, a terminal onto the terminal area to split, onto the editor to dock.
- **Folder tabs** (`FolderTabs.tsx`) sit in the top bar. A tab is a whole workspace with its own files, terminals, chat, graph
  and Private setting. Hidden folders use `visibility: hidden`, never `display: none`, because the panels read size 0 as
  collapsed and xterm cannot measure. A folder that is working says so in a word: Working, Waiting or Needs you. Close is
  disabled during a request, with the reason in its title, and unsaved work asks inline. Anything on `window` takes an
  `active` or `shown` prop, so a hidden folder never answers the keyboard.
- **Live tab** (`LiveView.tsx`): the work so far on the left, code on the right. The wording stays true: quiet is "No output
  for 1m 20s. The model may still be working.", never "stuck". Private shows a reply's code when it finishes, because
  Ollama holds tool calls back, and the tab says so. Added and removed lines keep their `+` and `-` signs.
- The prompt shows the current model as plain text. The picker waits for the owner's plan for the AI part.
- Slash commands live in `IDE.commands.list` in `lib/content.ts`, chat only, because a real shell owns its keyboard.
  One with a `prompt` is a premade request to the assistant; the rest run in `components/ide/useCommands.ts` and
  spend no tokens. `components/ide/Slash.tsx` holds the menu and the Up arrow history.
- The chat speaks to a non-programmer: plain progress first, tool details only when a step is opened.
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
