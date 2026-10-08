# Creek Stage Card — handoff for Claude Code

Two SVG designs in the Prism style (flat, 1px border, 18px radius, one big number with context), ready to wire into Home Assistant:

| File | What it is |
|---|---|
| `creek-stage-card.svg` | Full card, 400×266. A cross-section of the creek bank. The water rises and falls with the stage, and dashed Action / Flood / Major lines show the thresholds. |
| `creek-stage-feature.svg` | Compact **card feature**, 320×42. Its height and 12px radius match HA's `--feature-height` and `--feature-border-radius`, so it can sit under a tile card. |
| `creek-stage.js` | Reference updater (`updateCreekCard`, `updateCreekFeature`). It holds the exact mapping and text/pill sizing to port into `render()`. |
| `Creek Stage Card.html` | Interactive preview with a stage slider, trend slider, and light/dark themes. |

## Suggested elements
- Card: `custom:prism-creek-card`. Build it on the Prism pattern: shadow DOM, `PrismUI.TOKEN_STYLE`, a `PrismEditor` subclass, and `P.registerCard(...)`.
- Feature: `custom:prism-creek-stage-feature`. Push it to `window.customCardFeatures` with an `isSupported` check such as `(hass, ctx) => hass.states[ctx.entity_id]?.attributes.unit_of_measurement in {ft, m}`, following https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card-feature/. Read the entity from `this.context.entity_id`.

## Config (proposed)
```yaml
type: custom:prism-creek-card
entity: sensor.mill_creek_stage      # required — stage in ft or m
title: Creek Stage
flow_entity: sensor.mill_creek_flow  # optional — discharge (cfs / m³/s)
bed: 0          # stage at the channel bed (empty creek)
bank: 3.2       # bank crest height — water spills over the banks exactly here (defaults to flood)
action: 2       # warning / action stage (optional)
flood: 3.2      # flood stage (optional; its line is hidden when equal to bank)
major:          # major flood stage (optional)
max: 4.5        # top of the scene (defaults to bank × 1.4)
trend_hours: 1  # trend = change per hour over this window (recorder history)
accent: blue    # water colour
```

## Wiring map
- **Water level:** `#water` gets `transform="translate(0 Y)"`. Y is piecewise on `bank` (the crest height), so the drawn bank top always equals your real crest:
  - stage ≤ bank: `Y = 204 − (stage−bed)/(bank−bed) × 83`. This runs from the bed (y 204) up to the bank top (y 121).
  - stage > bank: `Y = 121 − (stage−bank)/(max−bank) × 29`. This runs from the bank top up to the top of the scene (y 92).
  - `#bank-crest-label` shows `BANK {bank} {unit}` at the crest.
  - Clamp the stage to `[bed, max]`. The water is drawn *behind* the bank path, so water above the bank line floods onto the land by itself.
  - The feature uses the same formula with bed 36, bank 13, top 4.
- **Thresholds:** `.th-action / .th-flood / .th-major` use `translate(0, Y(threshold))`. Hide any group whose threshold isn't configured. Label text goes in `#th-*-label`.
- **Status pill:** set `#stage-status-text` and the fill of `#stage-status-bg`:
  - Normal: `--prism-good`
  - Action (warning): `--prism-warn`
  - Flood: `--prism-bad`
  - Major: `--prism-purple` (#8a6fd6)
  - Resize the pill to fit the text, right-aligned at x 384.
- **Value:** `#stage-value` (1 decimal) and `#stage-unit`.
- **Trend:** `#stage-trend` shows `▲/▼ n ft/h`. Colors are inverted, because rising water is worse:
  - Rising: `--prism-bad`
  - Falling: `--prism-good`
  - Steady: `--prism-text-secondary`
- **Chips:**
  - `#chip-flow`: flow value
  - `#chip-peak`: 24h max, from history
  - `#stage-updated`: relative time since `last_changed`
  - Resize `#chip-*-bg` to fit their text.
- **Tap:** fire `hass-more-info` for `entity`.
- **Motion:** the SVGs carry only paint, set as presentation attributes. The card's shadow-DOM stylesheet supplies the motion using these rules:
  `.creek-water{transition:transform .8s ease-out} .creek-wave{animation:creek-wave 3.2s linear infinite} .creek-wave-sm{animation:creek-wave-sm 3.2s linear infinite} @keyframes creek-wave{to{transform:translateX(-24px)}} @keyframes creek-wave-sm{to{transform:translateX(-12px)}}`
  Disable both under `prefers-reduced-motion`.

## Tokens
All colours use `var(--prism-*, fallback)`, so the card also looks right without the Prism theme. There are two new terrain tokens, added to `tokens/colors.css` and needed in `themes/prism.yaml`:
- `prism-earth`: `#d8cfbd` light / `#3a342b` dark
- `prism-earth-edge`: `#c2b69e` light / `#4c4438` dark

## Before shipping
- Every element id in the SVG is fixed. If the card can appear more than once on a page, give ids like `creek-scene` a per-instance suffix (the preview does this).
- Text widths are measured with `getComputedTextLength()` after render, so run the sizing code after the SVG is attached.
