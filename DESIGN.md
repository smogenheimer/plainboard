---
name: PlainBoard
description: A timetable for a team of LLM agents. Ruled rows, one type size, vermilion only for what waits for you.
colors:
  carbon-bg: "oklch(0.185 0.006 95)"
  carbon-sunk: "oklch(0.228 0.007 95)"
  carbon-line: "oklch(0.305 0.008 95)"
  carbon-line-2: "oklch(0.42 0.01 95)"
  carbon-fg: "oklch(0.935 0.014 95)"
  carbon-fg-2: "oklch(0.78 0.013 95)"
  carbon-fg-3: "oklch(0.66 0.012 95)"
  carbon-you: "oklch(0.71 0.19 38)"
  carbon-you-fg: "oklch(0.76 0.16 40)"
  carbon-on-you: "oklch(0.17 0.03 38)"
  carbon-ok: "oklch(0.76 0.14 155)"
  stone-bg: "oklch(0.957 0.008 95)"
  stone-sunk: "oklch(0.925 0.01 95)"
  stone-line: "oklch(0.86 0.012 95)"
  stone-line-2: "oklch(0.72 0.014 95)"
  stone-fg: "oklch(0.2 0.01 95)"
  stone-fg-2: "oklch(0.38 0.012 95)"
  stone-fg-3: "oklch(0.49 0.012 95)"
  stone-you: "oklch(0.56 0.2 35)"
  stone-you-fg: "oklch(0.49 0.19 33)"
  stone-on-you: "oklch(0.99 0.005 95)"
  stone-ok: "oklch(0.47 0.12 155)"
typography:
  body:
    fontFamily: "Sofia Sans Semi Condensed Variable, Arial Narrow, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "14.5px"
    fontWeight: 400
    lineHeight: 1.4
    fontFeature: "tabular-nums"
  title:
    fontSize: "14.5px"
    fontWeight: 800
  label:
    fontSize: "14.5px"
    fontWeight: 600
    letterSpacing: "0.045em"
  mono:
    fontFamily: "ui-monospace, JetBrains Mono, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.9em"
rounded:
  all: "2px"
spacing:
  row-inset: "16px"
  column-inset: "10px"
components:
  button-primary:
    backgroundColor: "{colors.carbon-fg}"
    textColor: "{colors.carbon-bg}"
    rounded: "{rounded.all}"
    height: "32px"
    padding: "0 12px"
  button-primary-hover:
    backgroundColor: "{colors.carbon-fg-2}"
  button-secondary:
    textColor: "{colors.carbon-fg}"
    rounded: "{rounded.all}"
    height: "32px"
    padding: "0 12px"
  button-ghost:
    textColor: "{colors.carbon-fg-2}"
    rounded: "{rounded.all}"
    height: "32px"
    padding: "0 12px"
  input:
    textColor: "{colors.carbon-fg}"
    rounded: "{rounded.all}"
    height: "32px"
    padding: "0 10px"
  for-you-plate:
    backgroundColor: "{colors.carbon-you}"
    textColor: "{colors.carbon-on-you}"
    rounded: "{rounded.all}"
    height: "32px"
    padding: "0 10px"
---

# Design System: PlainBoard

## Overview

**Creative North Star: "The Railway Timetable"**

The board is a timetable: fixed columns, ruled rows, one type size. It shows who works on what and for how long, and puts what waits for the user first. It refuses the card-grid dashboard on a grey ground. The source of truth is `frontend/src/index.css`; shared controls live in `frontend/src/components/ui.tsx`.

**Key Characteristics:**
- Two named themes, `carbon` (dark) and `stone` (light), each one block of tokens.
- This document describes Fahrplan, the design the components are written against. PlainBoard carries other designs next to it (`frontend/src/designs/`, at present slop2gether, the look of the project's website, and Classic: cards, rounded corners, an indigo accent, Inter). slop2gether is what a browser shows that has not chosen; Classic and Fahrplan are one click away in the picker. Other designs restyle the same components and are not bound by the rules below.
- One type size. Rank comes from weight, case, reversal and rule.
- Hairline rules instead of cards. Flat surfaces, 2px corners.
- Vermilion means "this waits for you". Green only confirms.

## Colors

Warm neutrals at hue 95 with one signal colour. Values per theme are in the frontmatter (`carbon-*`, `stone-*`). In code the tokens are `--bg` and so on, exposed as Tailwind colours `bg`, `sunk`, `line`, `line-2`, `fg`, `fg-2`, `fg-3`, `you`, `you-fg`, `on-you`, `you-bg`, `ok`, `del`.

### Primary
- **Vermilion** (`you`): fill of the "for you" plate and the diamond mark. `you-fg` is vermilion as text or icon, `on-you` is text on a vermilion fill, `you-bg` is the tint behind a row that waits (`color-mix(in oklab, var(--you) 11%, var(--bg))`, derived for every theme). Outside the interface it is also the colour of the app's mark: favicon, app icon and the logo in the README are the three bars in `on-you` on a vermilion tile (`#ff6d3d`), the "for you" plate as a sign. Inside the interface the bars stay in the text colour, so the mark never competes with what waits.

### Secondary
- **Confirm green** (`ok`): the done check, an answered decision, a passed test, a finished stop.

### Neutral
- **Ground** (`bg`) and **sunk** (`sunk`): page, and hover, the user's own chat rows, opened details.
- **Rules** (`line`, `line-2`): row rules, and the stronger rule for field outlines and overlay edges.
- **Text** (`fg`, `fg-2`, `fg-3`): text, secondary text, muted text and placeholders. `fg` is also the 2px head rule, the focus outline and the plate fill.
- **Label squares**: `oklch(var(--sq-l) var(--sq-c) var(--h))`. The theme sets lightness and chroma (carbon 0.74 / 0.12, stone 0.56 / 0.15), the label sets the hue.

**The Semantic Only Rule.** Components use the semantic colours above, never a Tailwind palette colour (`zinc-500`, `red-600`) and never a hex value.

**The For You Rule.** Vermilion is reserved for what waits for the user or needs them: an open decision, a to-do, an error, a limit reached. Nothing else uses it.

**Diffs.** Added lines are `ok`, removed lines `del` (vermilion text in Fahrplan, the design's red in Classic), both as a 15% tint behind the line. Code colours stay semantic: keywords `fg` bold, strings `ok`, comments `fg-3` italic.

**The Confirm Rule.** Green only confirms that something is done or passed. It never decorates and never marks an action.

## Typography

**Body Font:** Sofia Sans Semi Condensed Variable, bundled via `@fontsource-variable` (fallback Arial Narrow, system-ui).
**Mono Font:** ui-monospace stack, for code and tool calls.

### Hierarchy
- **Title** (800): view title, dialog title, brand, headings inside chat prose.
- **Head** (`.caps`: 600, uppercase, 0.045em): column heads and section heads, on a 2px `fg` rule.
- **Plate** (`.plate`: `fg` fill, `bg` text, 600): the one strong element of a group, such as the primary button or the current view.
- **Strong** (500 to 600): ticket titles, names in chat, button labels.
- **Body** (400, 14.5px, line-height 1.4, tabular numbers): everything else. Muted text uses `fg-2` or `fg-3`, not a smaller size.

**The One Size Rule.** There is one type size. Do not add `text-sm`, `text-lg` or pixel sizes. Rank by weight, case, reversal and rule. Priority follows the same rule: low is muted, high is 600, urgent is caps at 800.

Built exceptions: on touch screens and below 640px the size is 16px, which keeps iOS from zooming into inputs. Code is 0.9em on fine pointers from 640px. The letter in an agent square is 11.5px.

## Layout

- **Desktop shell (from 1024px):** sidebar, main view, optional chat panel on the right (380px by default, 340px minimum, 75% maximum, resizable). Bars are 48px high.
- **Sidebar:** labelled at 184px from 1280px, or from 1440px while the chat panel is open. Otherwise a 50px icon rail. The labelled sidebar holds the "Running now" table.
- **Phone and tablet (below 1024px):** 52px top bar with project switcher and the "for you" plate, bottom tabs (54px) with four views and a "More" sheet. "Running now" becomes one scrolling line above the board.
- **Board (from 768px):** five columns side by side, at least 172px each, scrolling sideways. Below 768px the statuses stack and fold.
- **Rows:** inset 16px on phones and in chat, 10px inside board columns. Each row ends in a 1px `line` rule.
- **Touch sizes:** controls grow to 44px through the `touch:` variant (`@custom-variant touch` in `index.css`: below 640px or a coarse pointer, the same condition as the 16px text).

## Elevation & Depth

Flat. Depth comes from rules and from the `sunk` tone. There is one shadow, `--shadow-lg`, and only overlays carry it: dialogs, menus, the select list, a ticket while it is dragged. Overlays also get a 1px `line-2` edge. Dialogs sit on a black scrim.

**The Flat Rule.** A surface at rest has no shadow. If it does not float above the page, it gets a rule, not a shadow.

## Shapes

One corner: every `rounded-*` resolves to 2px. Fields and secondary buttons are outlined with an inset 1px `line-2` ring. Focus is a 2px `fg` outline with 2px offset; fields switch to a 2px `fg` ring.

**The Fixed Cell Rule.** State is a 9px mark in a fixed first cell of the row (`.mk`): filled circle = working, hollow circle = queued or in the background, vermilion diamond = waits for you, green check = done, empty = idle. State is never told by row colour alone or by a badge.

## Components

- **Buttons** (`Button`): primary is a plate, secondary is outlined, ghost is text that sinks on hover, danger looks like secondary (destroying something is not something that waits for the user, so it is not vermilion). Default variant is secondary. One primary per group.
- **Page head** (`PageHead`): every view starts with the same 48px bar, title at the left, actions at the right, a hairline under it. The board and the chat header sit on the same line.
- **Segmented** (`Segmented`): a choice between a few options as one joined control; the chosen option is the plate.
- **For you** (`ForYou`): the vermilion plate with the count of waiting tickets. On desktop it sits at the top of the sidebar, on phones in the top bar; it lists the tickets behind it.
- **Chat context column** (`ChatAside`): from 1280px the full-page chat shows, at its right, the waiting tickets as a list, the agent's assignments (for the manager: what it handed out) and the plan limits written out.
- **Limit rings** (`LimitsRing`): under every composer, one 16px ring per subscription, filled to its fullest window, with the account's name; a click opens the windows, resets and forecast. Usage is always in view without scrolling. The ring is the one round mark of the system: track `line-2`, arc `fg-2`, `fg` from 75%, vermilion from 90%.
- **Fields** (`Input`, `Textarea`, `Select`, `Field`, `Toggle`): transparent with an outline. `Select` replaces the native element so the list matches the theme. No multi-line field has a drag handle (`resize: none` everywhere): it grows with its text from its `rows` up to a maximum height (`Textarea` defaults to 60vh, composers a few lines) and scrolls from there. `Textarea` does this by itself; a hand-built `<textarea>` uses `useAutoGrow` from `lib/hooks.ts`. Full-page editors (doc page, doc rules) grow without a maximum.
- **Ticket row** (`TaskCard`): three zones that never mix. The title with its number. What waits for the user. Then the tags at the left (priority first: low muted, high outlined in the text colour, urgent as a plate; each label an outlined 20px mark, led by a colour square only if the label has a colour) and the facts at the right as one group in a fixed order: comments, files, public, who works on it, for how long. The facts drop to their own line, still at the right, when the tags fill theirs.
- **Running now** (`RunningNow`): grid with fixed columns: mark, agent, ticket, time.
- **Chat** (`MessageItem`, `CHAT_ROW`): every entry is a ruled row with name left and time right. The user's rows are `sunk`. There are no bubbles.
- **Dialog** (`Modal`): full screen below 640px. Title at 800 on a 2px `fg` rule.
- **Settings** (`SettingsLayout`, `SaveRow`): settings are never one long form. They are a list of sections with one open: the list at the left from 672px of own width (a container query, so it also holds next to the chat panel), a scrolling row above the section below that. A section starts with its caps head on the 2px rule and one or two sentences on what is decided there. A section that is a form ends in its own Save, which says "Not saved yet" and "Saved"; a list that stores every change by itself says so. Every field has a persistent label and a hint that says what it does and what empty means; placeholders are only examples. The project's settings are a view in the navigation (`ProjectSettings`), the installation's a page over the app (`SettingsDialog`), the two wizards (`features/onboarding/`) use the same section names.
- **Labels** (`Label`): a 7px coloured square plus the name as text. **Agents** (`AgentSquare`): the first letter on the agent's own colour.
- **Icons:** `lucide-react`, 16px, 20px in phone navigation.

## Do's and Don'ts

### Do:
- **Do** build lists as ruled rows: `border-b border-line`, hover `bg-sunk`.
- **Do** reuse `ui.tsx`, `.caps`, `.plate` and `.mk*` before writing new classes.
- **Do** add touch sizes with `touch:`.
- **Do** add a theme like this: copy a token block in `index.css` and name it `[data-theme="<id>"]`, and list it in `DESIGNS` in `lib/theme.ts` with its browser bar colour. There is no `dark:` variant: components only use the tokens.
- **Do** give a new part of the interface a `data-part` name, so that other designs can draw it their way (see "Add a design or a theme" in `ARCHITECTURE.md`).

### Don't:
- **Don't** use palette colours or hex values in components.
- **Don't** use vermilion for emphasis, branding or a primary action.
- **Don't** use green for anything that is not a confirmation.
- **Don't** change the type size to show rank.
- **Don't** wrap content in cards with borders, radius and shadow.
- **Don't** add a shadow to anything that is not an overlay, or a radius other than 2px.
