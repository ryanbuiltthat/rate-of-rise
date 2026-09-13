# Companion-app home screen widget — Creek Alert Tier

A phone home-screen widget showing the current alert tier, styled to match the add-on's own
branding (`rate_of_rise/icon.png` / `logo.png`): amber header, tier-colored headline, the
same tier glyphs already used on the [dashboard](../dashboards/creek_flood_watch.yaml) and in
the [tier-change push](../ha-packages/creek_warning.yaml).

This is **on-device** configuration inside the Home Assistant companion app — there is
nothing to copy into `/config`, and no add-on version bump. This doc is the copy-paste
source for it.

Option names below are from the companion app's own docs
([Android](https://companion.home-assistant.io/docs/integrations/android-widgets/) ·
[iOS](https://companion.home-assistant.io/docs/integrations/ios-widgets/)).

## Brand palette

Sampled directly from `rate_of_rise/icon.png` / `logo.png`, not eyeballed:

| Hex | Used for |
|---|---|
| `#0D3B54` | Card background (navy) — *not reachable from a widget; see below* |
| `#F2A900` | Warning-triangle amber — the header, and tiers 2–3 |
| `#3D9BD4` | Mid wave blue — tier 1 |
| `#6CC4E8` | Light wave blue — tier 0 |
| `#F0F6FA` | Text on navy |

**Widget backgrounds cannot be branded.** Every Android widget's *Widget theme* offers only
*Dynamic color*, *Light/dark theme*, and *Transparent* — there is no background color field,
so the navy card cannot be reproduced behind the text. What *is* fully controllable is the
text, via HTML in the template (below), so the branding comes through as colored type rather
than a colored card.

Tier colors escalate through the palette — `#6CC4E8` → `#3D9BD4` → `#F2A900` — with one
deliberate departure: Tier 4 Emergency uses `#E5484D`, a red the add-on's artwork does not
contain. The palette tops out at amber, and an Emergency that looks the same as a Watch is
worse than an off-brand color.

## Android — Template widget

The Template widget renders **HTML**, which is where the color comes from. Documented
supported tags: `<br>`, `<b>`, `<big>`, `<font color='#RRGGBB'>`, and
`<p style="text-align: end">`.

1. Long-press the home screen → **Widgets** → **Home Assistant** → **Template**, and drop it
   on the home screen. (To edit one already placed: long-press it — most launchers show a
   gear/pencil icon on widgets that support reconfiguration, which reopens this same screen.
   Otherwise remove it and re-add.)
2. **Template text** — tap the field, **select all and delete the existing placeholder text**
   first (it reads "Enter template here" and does not clear itself — pasting on top of it
   just concatenates the two), then paste:

   ```jinja2
   {% set e = 'sensor.rate_of_rise_creek_alert_tier' %}
   {% set tier = states(e) %}
   {% set dead = tier in ['unknown', 'unavailable'] %}
   {% set glyph = {'0': '✅', '1': '🌧️', '2': '👀', '3': '⚠️', '4': '🚨'}.get(tier, '❔') %}
   {% set color = {'0': '#6CC4E8', '1': '#3D9BD4', '2': '#F2A900', '3': '#F2A900', '4': '#E5484D'}.get(tier, '#F2A900') %}
   {% set headline = 'No data — add-on offline' if dead else (state_attr(e, 'label') or 'Unknown') ~ ' · Tier ' ~ tier %}
   {% set reason = 'Check the Rate of Rise add-on' if dead else (state_attr(e, 'why') if (tier != '0' and state_attr(e, 'why')) else 'No elevated flood risk') %}
   {{ [
        "<font color='#F2A900'><b>CREEK</b></font>",
        "<font color='" ~ color ~ "'><big>" ~ glyph ~ " " ~ headline ~ "</big></font>",
        reason,
        '' if dead else states('sensor.rate_of_rise_creek_flood_probability') ~ '% flood probability'
      ] | reject('equalto', '') | join('<br>') }}
   ```

3. **Widget theme** → **Transparent**, which is the only option that also exposes a text/icon
   color, and lets the colored type sit on the wallpaper the way the add-on's card sits on
   navy. *Light/dark theme* works too — the `<font>` colors still apply — but amber on a
   white background is noticeably lower contrast.
4. **Widget text size** → 12–14; four rows clip at the default on most launchers.

Three things about that template worth knowing before editing it:

- **Rows are joined with `<br>`, not newlines.** A raw newline in the template collapses to a
  space (the same rule Markdown follows), which is why an earlier version of this doc
  rendered as one run-on line. The rows are built as a list and joined so no stray
  source-file whitespace can leak into the output.
- **The emoji are the one color channel that needs no markup** — they render full-color
  regardless of theme or `<font>`, which is why the glyph carries severity even if the HTML
  is ever stripped.
- **An offline add-on says so.** The MQTT LWT flips the tier entity to `unavailable` when the
  add-on stops, which would otherwise render as "Tier unavailable" beside a stale-looking
  card. `dead` turns that into an explicit "No data — add-on offline" and drops the
  probability row, because a silent-looking widget on an alerting system is the failure mode
  this project cares most about (cf. the service-stale watchdog in `creek_warning.yaml`).

### The fully branded card (not built)

The **Picture** widget displays a camera snapshot or image entity, so a genuinely branded
card — navy background, the warning triangle, the wave motif, live text — is reachable by
having the add-on render a PNG per fast loop and publish it as an MQTT camera (auto-created
via discovery, like everything else it publishes).

It is not built, and the reason is in the widget's own docs: Picture updates **hourly, or on
tap**. For a basin where rainfall-to-crest is tens of minutes (spec §1), an alert surface
that can be an hour stale is worse than an ugly one that is current. Revisit only if the
refresh behavior changes.

## iOS

iOS has no HTML and no Template widget, but **Custom Widgets (BETA)** expose exactly the
fields Android's are missing — "icon, icon color, display text, text color, background
color" — so the navy card *is* reachable there. Set background `#0D3B54`, text `#F0F6FA`,
icon color `#F2A900`, and point the widget at
`sensor.rate_of_rise_creek_alert_tier`.

The non-beta alternatives:

- **Details** — up to 3 templated lines. Use the tier, the reason
  (`sensor.rate_of_rise_creek_tier_reason`), and the probability.
- **Gauge** — templated "Value template" / "Value label template", a good fit for
  `sensor.rate_of_rise_creek_flood_probability` (0–100).
- **Sensors** — plain values, ~15 minute update interval.

Icons need no matching work: these entities already carry the `mdi:` icons the add-on
assigns them via MQTT discovery (`app/discovery.py`), so the widget's icon is the add-on's
icon rather than a copy of it.

## Notes

- Every widget here is a read-only display. The actual alerting — critical push, alarm-stream
  sound, persistent notification — is the `creek_tier_change` automation in
  `ha-packages/creek_warning.yaml`; see
  [DOCS.md](../rate_of_rise/DOCS.md#companion-home-assistant-config). Don't rely on a
  widget being looked at during a storm; rely on the push.
- Tier 0 always reads "No elevated flood risk" even if `why` is populated — the same guard
  the dashboard's markdown card uses, so an all-clear never echoes a stale reason.
- All thresholds behind the tier are placeholders (`app/tiers.py`), so a widget reading
  Watch or Warning is a prompt to go look, not a calibrated alarm — the same caveat that
  follows this tier everywhere else it appears.
