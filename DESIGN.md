---
name: TFA
description: Ticker Finder Analysis. Small US companies that pass eight quality checks, ranked by a transparent 100-point score, every claim beside the figure that proves it.
colors:
  bg: "#090d14"
  bg-raise: "#0c111a"
  surface: "#0f1520"
  surface-2: "#141b28"
  surface-3: "#1a2232"
  surface-4: "#222b3d"
  line: "#1c2433"
  line-2: "#273042"
  line-3: "#364156"
  text: "#e8ecf2"
  text-2: "#a3adbd"
  text-3: "#7c879a"
  inverse: "#e8ecf2"
  inverse-hover: "#ffffff"
  on-inverse: "#090d14"
  up: "#3fbf8c"
  up-dim: "rgba(63, 191, 140, 0.12)"
  down: "#e2706a"
  down-dim: "rgba(226, 112, 106, 0.12)"
  mid: "#c3ccda"
  mid-dim: "rgba(195, 204, 218, 0.1)"
  tape: "#c3ccda"
  tape-track: "#2b364b"
typography:
  display:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(32px, 4.2vw, 52px)"
    fontWeight: 600
    lineHeight: 1.06
    letterSpacing: "-0.035em"
    fontVariation: "'wdth' 92"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(26px, 2.6vw, 33px)"
    fontWeight: 600
    lineHeight: 1.12
    letterSpacing: "-0.028em"
    fontVariation: "'wdth' 92"
  title:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "clamp(21px, 2.1vw, 25px)"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.022em"
    fontVariation: "'wdth' 92"
  subhead:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: "-0.012em"
  body:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  lede:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
  data:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.55
    fontFeature: "'tnum' 1"
  label:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.4
  ticker:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    letterSpacing: "0.01em"
  figure:
    fontFamily: "Archivo, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    letterSpacing: "-0.02em"
    fontFeature: "'tnum' 1"
rounded:
  frame: "8px"
  ctl: "6px"
  tag: "4px"
spacing:
  gutter: "24px"
  gutter-phone: "16px"
  frame-inset: "18px"
  panel: "20px"
  section: "72px"
  band: "88px"
  container: "1240px"
  nav-height: "56px"
components:
  button-primary:
    backgroundColor: "{colors.inverse}"
    textColor: "{colors.on-inverse}"
    rounded: "{rounded.ctl}"
    height: "38px"
    padding: "0 16px"
  button-primary-hover:
    backgroundColor: "{colors.inverse-hover}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.ctl}"
    height: "38px"
    padding: "0 16px"
  button-ghost-hover:
    backgroundColor: "{colors.surface-2}"
  button-sm:
    height: "32px"
    padding: "0 12px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.ctl}"
    height: "44px"
    padding: "0 14px 0 40px"
  input-focus:
    backgroundColor: "{colors.surface-2}"
  nav-link:
    textColor: "{colors.text-2}"
    rounded: "{rounded.ctl}"
    padding: "6px 10px"
  nav-link-current:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.text}"
  pill:
    backgroundColor: "transparent"
    textColor: "{colors.text-2}"
    rounded: "{rounded.ctl}"
    height: "30px"
    padding: "0 11px"
  pill-pressed:
    backgroundColor: "{colors.inverse}"
    textColor: "{colors.on-inverse}"
  seg-button:
    backgroundColor: "transparent"
    textColor: "{colors.text-2}"
    rounded: "{rounded.tag}"
    height: "26px"
    padding: "0 10px"
  seg-button-pressed:
    backgroundColor: "{colors.surface-4}"
    textColor: "{colors.text}"
  tag:
    backgroundColor: "{colors.surface-3}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.tag}"
    height: "21px"
    padding: "0 7px"
  tag-up:
    backgroundColor: "{colors.up-dim}"
    textColor: "{colors.up}"
  tag-down:
    backgroundColor: "{colors.down-dim}"
    textColor: "{colors.down}"
  tag-mid:
    backgroundColor: "{colors.mid-dim}"
    textColor: "{colors.mid}"
  tag-solid:
    backgroundColor: "{colors.inverse}"
    textColor: "{colors.on-inverse}"
  flag:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.tag}"
    size: "22px"
  flag-warning:
    textColor: "{colors.down}"
  frame:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.frame}"
  table-head:
    backgroundColor: "{colors.bg-raise}"
    textColor: "{colors.text-3}"
    padding: "10px 12px"
  table-cell:
    typography: "{typography.data}"
    textColor: "{colors.text}"
    padding: "9px 12px"
  score-tape:
    backgroundColor: "{colors.tape-track}"
    height: "8px"
    width: "120px"
  ledger:
    backgroundColor: "{colors.bg-raise}"
    padding: "20px 22px 24px"
---

# Design System: TFA

## Overview

**Creative North Star: "The Open Ledger"**

TFA reads like a ledger left open on the desk: one cool scale from navy-black to near-white, hairline seams, and figures in true tabular numerals. Every claim a page makes sits beside the live figure that proves it, and every score can be opened to show its working, part by part. The system exists to make numbers legible and checkable, then stay out of their way.

Density is high by default. Tables run at 13.5px with nine-pixel cell padding, and the ranked list starts above the fold. Explanation is kept one step away (a disclosure, an opened row, a hover tip) rather than removed. Hierarchy comes from two weights, steps of lightness on the neutral scale and 1px seams, never from hue. Color appears only when a figure has a direction: emerald when it is up or in the company's favor, a muted brick red when it is down or against it.

The world refuses the dark fintech dashboard: rounded cards on drop shadows, neon accents, glows and decorative charts. Its one signature is the 100-point score tape, ten segments each as wide as its part's maximum and filled to the points it earned. The brand mark is the same tape in miniature: two parts earned, one not.

**Key Characteristics:**
- One cool neutral scale from Navy-Black (#090d14) to Cool Near-White (#e8ecf2); no accent hue.
- Color is a sign: emerald for up or in favor, muted red for down or against, pale steel for a middle reading.
- Archivo at two weights (400, 600) with tabular figures; h1 and h2 set at 92% width with negative tracking.
- Flat frames: a 1px hairline border, an 8px corner, no shadow.
- The primary button is the scale's lightest step, inverted, not a hue.
- The score tape and its ledger are the signature components.
- One authored motion per page on an expo-out curve, always gated by reduced motion.

## Colors

One cool, faintly blue neutral scale carries the entire interface; two sign colors are the only hues, and they speak only about direction.

### Primary
- **Inverse Near-White** (#e8ecf2, hover #ffffff, text on it #090d14): the primary action ("Analyze", "Open the NATR report"), the pressed sector pill, the Tier 1 tag, the skip link and the brand mark's tile. The primary is the lightest step of the neutral scale turned into a surface; it is never tinted.

### Secondary (the sign pair)
- **Sign Emerald** (#3fbf8c; tag ground at 12%): a figure that is up, or a reading in the company's favor. Positive changes, "Undervalued", "Healthy", "Passes", a rising price line, a strong health meter, and the check marks beside rules a company passed.
- **Sign Red** (#e2706a; tag ground at 12%): a figure that is down, or a reading against the company. Negative changes, "Overvalued", "Weak", a falling price line, negative revenue bars, a weak health meter, and the icon of the one warning flag (above the upper band).

### Neutral
- **Navy-Black Ground** (#090d14): the page, the sticky feed-day headings, text on the inverse primary, and the browser theme color.
- **Raised Ground** (#0c111a): table header rows, an opened row's ledger, the section rails, and the report's context-only health groups. One step up from the page, below a frame.
- **Frame Slate** (#0f1520): every frame, table body, input and disclosure.
- **Hover Slate** (#141b28): hovered rows, links and ghost buttons, the focused input, the flag tile, the suggestion list.
- **Current Slate** (#1a2232): the current nav link and the neutral tag's ground.
- **Pressed Slate** (#222b3d): the pressed segment, the highlighted suggestion, the scrollbar thumb.
- **Hairline Seam** (#1c2433): every frame border and row divider. The most-used line on the site.
- **Control Edge** (#273042): outlines of inputs, ghost buttons, pills and the segmented control, and the flag tile's inset ring.
- **Strong Seam** (#364156): hover borders, the rule above a table's total, link underlines, the outlined tag, chart baselines.
- **Cool Near-White** (#e8ecf2): primary text and every figure.
- **Fog** (#a3adbd): secondary text, ledes, company names under tickers, a ledger's "measured on" column.
- **Slate Label** (#7c879a): column heads, counts, notes, placeholders and icons at rest (5.0:1 on Frame Slate).
- **Pale Steel** (#c3ccda; ground at 10%): a middle reading ("Fair", "Mixed", "Misses one rule", "Not confirmed") and the earned fill of the score tape.
- **Tape Track** (#2b364b): the unearned part of each tape slot, the meter's track, the fair-value band.

### Named Rules
**The Sign-Only Rule.** Emerald and red state direction and nothing else: a figure's sign, or a word that is itself a reading for or against the company. Flags, category tags (tier, source), the score tape and every structural element stay on the neutral scale, the icon or the word carrying the meaning.

**The No Third Hue Rule.** A middle reading is Pale Steel (#c3ccda), never amber, yellow or blue. Amber was retired from this system because a third hue turns "fair" into a warning.

**The Inverse Primary Rule.** The primary action is Inverse Near-White with Navy-Black text. There is no brand hue to put on a button, a link or a focus ring; focus is a 2px Cool Near-White outline.

## Typography

**Display Font:** Archivo (with ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif)
**Body Font:** Archivo (same stack)
**Label/Mono Font:** Archivo semibold for tickers; the site has no monospace

Archivo loads from Google Fonts as a variable face on two axes (width 75 to 100, weight 400 to 600).

**Character:** One grotesque does everything. Headings tighten by width and tracking rather than by weight, so a claim reads compact and firm without shouting, and every number sits in true tabular figures so a column aligns digit by digit.

### Hierarchy
- **Display** (600, clamp(32px, 4.2vw, 52px), 1.06, 92% width): the home hero's claim only, held to 19ch.
- **Headline** (600, clamp(26px, 2.6vw, 33px), 1.12, 92% width): each page's h1, which is its claim; it may carry live counts ("77 of 4,229 companies pass all eight quality checks"). The report's company name runs clamp(26px, 2.8vw, 34px) and the home page's claim heads clamp(24px, 2.6vw, 32px).
- **Title** (600, clamp(21px, 2.1vw, 25px), 1.2, 92% width): section h2s, with their count or date in Slate Label beside them on the baseline.
- **Subhead** (600, 16px, 1.35, normal width): h3, a frame's own title ("Price"), the feed's day headings; company blocks run 15px.
- **Body** (400, 15px, 1.55): running text. The lede is 16px in Fog at 64ch; section ledes are 14.5px at 76ch.
- **Data** (400, 13.5px, tabular): table cells; ledger tables run 13px.
- **Label** (400, 12.5px, 1.4): notes, captions, counts and frame-head meta in Slate Label; column heads are 12px, and the labels on phone blocks 11px to 11.5px.
- **Ticker** (600, 13px, +0.01em): every ticker, stacked above its company name in Fog at 12.5px.
- **Figure** (600, tabular, tracking -0.01em to -0.035em): proof-line values at 15px, insider purchase figures at 17px, ledger totals and stat values at 22px, the report's price at 30px, the health score at 44px.

### Named Rules
**The Two Weights Rule.** 400 and 600 only. Emphasis inside a sentence is 600 in Cool Near-White; anything more comes from size, width and the neutral scale.

**The Tabular Figures Rule.** Every figure uses tabular numerals and is right-aligned in its column, its column name aligned with it. A number that does not line up digit by digit is a defect.

**The Sentence Case Rule.** Headings, labels, tags and buttons are sentence case. Nothing is set in uppercase or tracked caps, and no label sits above a heading.

## Layout

Content sits in one centered column (1240px) with a 24px gutter that drops to 16px under 640px. A sticky 56px header carries the mark, the three tools, the data date and a ticker search; its ground is Navy-Black at 86% with a 12px backdrop blur and a hairline below. Under 900px the data date leaves the header and joins the page's claim; under 760px the search goes and the tool links take the right edge.

Pages open with a claim block: the h1 across the top, the lede below it on the left, and on the right, bottom-aligned with the lede, a row of live figures (value above, Slate Label name below). Under 860px it stacks. On the screener the ranked table begins inside the first viewport at 1280x800 with at least eight rows. The home page alternates claim and proof side by side (0.9fr to 1.1fr, 64px apart), the proof switching sides each band, stacking under 960px.

Vertical rhythm is generous between sections and tight inside them: 72px between sections (64px on the report), 88px home bands (56px under 960px), 112px before the footer, and more space above a heading than below it (12px to its content). Inside frames, rows inset 18px from the edge; panels pad 20px (16px on phones).

The screener's table keeps its column names under the site header while the list scrolls past. A table wider than its frame scrolls inside the frame, never the page: the rank and company columns stay put and the right edge fades over 36px. On phones tables reflow into labelled blocks: the screener under 720px (rank, company and score on the first line, flags on the second, then eight figures in a four-column grid each with its label), the insider buyers and stakes under 960px, the report's insider table under 640px.

### Named Rules
**The Claim Beside Proof Rule.** A page or section opens with one short claim and, beside it, the live figures that make it true: a row of counts, a top-five frame, a ledger. A claim with no figure next to it does not ship.

**The No Page Scroll Rule.** Nothing scrolls the page sideways at any width. Wide tables scroll inside their frame with sticky leading columns, or reflow into labelled blocks.

## Elevation & Depth

The system is flat and builds depth by tonal layering: Navy-Black page, Raised Ground for table heads and opened ledgers, Frame Slate for frames, then Hover, Current and Pressed Slate for interaction states. Seams, not shadows, separate things. Shadow is reserved for layers that genuinely float over content, plus one deliberate exception: the home page's proof frames, which present the product's own figures and sit a little above the ground with a soft offset shadow and a 1px highlight of light on their top edge.

### Shadow Vocabulary
- **Pop** (`box-shadow: 0 18px 44px -14px rgba(0, 0, 0, 0.75), 0 2px 6px -2px rgba(0, 0, 0, 0.5)`): floating layers only, the ticker search's suggestion list and the revenue chart's hover tip.
- **Proof lift** (`box-shadow: 0 24px 48px -24px rgba(0, 0, 0, 0.75), 0 2px 8px -4px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.045)`): the home page's proof frames only.
- **Inset ring** (`box-shadow: inset 0 0 0 1px #273042`): an edge drawn without a border, on flag tiles, derived revenue bars and the fair-value band. Not elevation.

### Named Rules
**The Flat Frame Rule.** A frame is Frame Slate, a Hairline Seam border and an 8px corner, with no shadow. Shadow belongs to what floats (popovers, tooltips) and to the home page's proof frames; nowhere else.

**The One Painted Ground Rule.** The only painted background is the home hero's faint radial lift of Pale Steel at 7% behind the proof. Every other gradient is functional: the fade at a scrolling edge and the loading shimmer.

## Shapes

Corners step down with size: frames 8px, controls (buttons, inputs, pills, links in rails, nav links) 6px, tags, flag tiles and segment buttons 4px, tape slots 1px, the meter 2px. Only the latest-price dot on the chart is round. Every line is 1px: frame borders, row seams, the rules above totals, the seams between health groups (drawn as 1px shadows so the grid's cells share them). The one 2px line is the focus outline. Frames never nest: a disclosure inside a frame becomes its last row, a hairline across the top with no border or corner of its own.

Icons come from one sprite drawn on a 24px grid with 1.75px round strokes in currentColor, sized at 1.15em so they follow the text around them. The brand mark is a near-white rounded tile (7 of 32 units) carrying three tape slots in Navy-Black, the last at 32% opacity.

## Components

### Buttons
Quiet and decisive: one light slab for the action, hairline outlines for everything else.
- **Shape:** gently squared (6px), 38px tall, 16px sides; small 32px tall, 12px sides.
- **Primary:** Inverse Near-White ground, Navy-Black 14px semibold text, an optional trailing arrow icon at 15px.
- **Hover / Focus:** primary brightens to pure white; ghost takes Hover Slate and a Strong Seam border; 150ms color transitions; focus is a 2px Cool Near-White outline 2px out.
- **Ghost:** transparent with a Control Edge outline and regular-weight Cool Near-White text, for secondary paths ("See insider buying", "Show more", "Reset").
- **Text link:** 14px Cool Near-White with a trailing arrow that moves 2px right on hover.

### Pills and Segmented Control
- **Pills:** a sector or tier choice with its count; 30px, 6px corner, Control Edge outline, Fog text, count in Slate Label. Pressed inverts to Inverse Near-White with Navy-Black text. An empty choice dims to Slate Label on a Hairline Seam. On phones the row scrolls sideways inside itself with a faded right edge.
- **Segmented control:** a Control Edge outline holding 26px buttons (4px corner); the pressed one is Pressed Slate with Cool Near-White text. On phones it stretches to full width in equal columns and lets labels wrap.

### Tags
- **Style:** a short word, 21px tall, 4px corner, 12px semibold. Neutral by default: Fog on Current Slate.
- **Variants:** sign tags (emerald or red text on a 12% tint of itself) only for a reading with a direction; the middle reading in Pale Steel on a 10% tint; solid inverse for Tier 1, the neutral tag for Tier 2, outlined (Strong Seam, transparent) for smaller purchases.

### Flags
Context, not verdicts. A 22px tile (4px corner, Hover Slate, Control Edge inset ring) holding a 14px icon in Fog; flags are told apart by their icons and named for screen readers and in a legend. Only the warning flag's icon is Sign Red. A row shows four; past that the last tile counts the rest ("+2").

### Cards / Containers
- **Corner Style:** 8px.
- **Background:** Frame Slate; Raised Ground for table heads, rails and context groups.
- **Shadow Strategy:** none (see the Flat Frame Rule); home proof frames carry Proof lift.
- **Border:** 1px Hairline Seam.
- **Internal Padding:** 20px panels (16px on phones); rows and heads inset 18px; a proof frame has a head line (title left, meta right), its live rows, and a foot link aligned right.

### Inputs / Fields
- **Style:** 44px, Frame Slate, Control Edge border, 6px corner, a 17px search icon in Slate Label at the left, placeholder in Slate Label.
- **Hover / Focus:** border to Strong Seam on hover; on focus the border goes to Fog and the ground to Hover Slate.
- **Suggestions:** a popover list (Hover Slate, 8px corner, Pop shadow) of ticker, name and sector; the highlighted row is Pressed Slate. "/" anywhere focuses the header search, shown by a small key hint that hides once typing starts.

### Navigation
A single sticky bar: mark and "TFA" (the long name in Slate Label until 1100px), three tool links in Fog at 14px with 6px corners, Hover Slate on hover and Current Slate with Cool Near-White text for the current page. The data date and a 260px search sit at the right. On phones only the mark and the three links remain, tightening to 13px.

### Tables
Header row on Raised Ground with 12px Slate Label names, regular weight, right-aligned over figures and left-aligned over text; a sort arrow appears on hover and stays on the sorted column, hanging left of a right-aligned name so it takes no width. Cells are 13.5px, 9px by 12px, separated by Hairline Seams; totals sit under a Strong Seam in semibold. Hovered rows go to Hover Slate.

### Score Tape (signature)
The score as a numeral (15px semibold, tabular) beside an 8px tape: ten slots in a grid, each slot's width proportional to its part's maximum points, 2px apart, filled in Pale Steel to the share of points earned over a Tape Track ground. The large tape is 10px. The score is never colored by value.

### Score Ledger (signature)
Opening a screener row unfolds its ledger in place on Raised Ground: three columns (score parts with each part's own figure and a 30px points bar, the eight rules with the company's figure beside each limit and an emerald check, and its flags in words with a primary button to the report), two columns under 1100px, one under 720px. The report shows the same ledger, and the home page shows its score column alone as proof.

**The Tape Rule.** A score is always a numeral plus its tape, and the tape always shows every part; it is never a colored badge, a ring or a green-to-red bar.

### Meter
A health measure from 1 to 100: a 4px track in Tape Track with a fill in Sign Emerald (strong), Pale Steel (fair) or Sign Red (weak), and its value at 13px tabular beside it.

### Disclosure
A Frame Slate summary line (14px semibold, a lead icon in Slate Label, a caret at the right that turns 180 degrees) that opens to the full explanation; Hover Slate on hover.

### Motion
**The One Motion Rule.** Each page has one authored motion, on the expo-out curve `cubic-bezier(0.16, 1, 0.3, 1)`, and content is fully visible without it. The screener's opened ledger unfolds top-down by clip-path over 420ms; insider companies added by "Show more" arrive the same way over 500ms; on the report the price marker travels from the fair value's midpoint to today's price over 1.1s while the gap between them draws. Everything else is a 150ms color change. Under reduced motion every transition is instant and no animation runs.

## Do's and Don'ts

### Do:
- **Do** put the figure that proves a claim beside it: a row of live counts by the h1, a top-five frame by the home hero, a ledger by "why it ranks first".
- **Do** keep color to direction: Sign Emerald (#3fbf8c) for up or in favor, Sign Red (#e2706a) for down or against, each on a 12% tint of itself when it is a tag.
- **Do** set every figure in tabular numerals, right-aligned, and every ticker in Archivo 600 at +0.01em over the company name in Fog.
- **Do** separate with 1px seams (#1c2433) and group with one frame (Frame Slate, 8px corner) rather than with shadows or extra containers.
- **Do** step interaction states up the neutral scale: Hover Slate, then Current Slate, then Pressed Slate.
- **Do** tell flags apart by icon on neutral tiles, with a legend and screen-reader names.
- **Do** reflow tables into labelled blocks on phones and keep the page free of sideways scroll.
- **Do** draw icons from the sprite (24px grid, 1.75px round strokes, currentColor, 1.15em).

### Don't:
- **Don't** build the dark fintech dashboard this world refuses: rounded cards on drop shadows, neon accents, glows or decorative charts.
- **Don't** add a third hue, including amber or yellow for a middle reading; it is Pale Steel (#c3ccda).
- **Don't** color the score, a flag tile or a category tag by its value; the tape fills in Pale Steel and only the warning flag's icon is red.
- **Don't** put a shadow on a frame other than the home page's proof frames, and don't nest a frame inside a frame.
- **Don't** use weights other than 400 and 600, a monospace for tickers, or uppercase and tracked caps anywhere.
- **Don't** paint backgrounds beyond the home hero's single 7% radial lift.
- **Don't** add motion beyond each page's one authored moment, or animate anything that hides content until it plays.
