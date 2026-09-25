# Open Questions

Mirrors §9 of the [project spec](../creek-flood-warning-spec.md) (the source of truth) and
extends it with field-calibration items surfaced during build-out. Strike through and note
the resolution as each is answered.

## v1 — 2026-09-12

**v1 is the line under the build phase, not under the calibration phase.** Everything in the
signal path is built, wired and tested end to end: the node measures, the radio link carries
it, the gateway converts distance to depth, the add-on ingests eleven data sources, the tier
ladder evaluates, and a Tier ≥ 2 sounds a critical alarm on the phones through Do Not Disturb.

What v1 explicitly does **not** claim is calibration. Every threshold in `app/tiers.py` is a
placeholder, and it stays a placeholder until real storms say otherwise — there is no way to
shortcut that, because the numbers that matter are properties of this basin and can only be
measured by watching it rain. The alerts are honest about it: every notification carries
"thresholds are not yet field-calibrated — verify before acting."

So the backlog below is not a list of things that went unfinished. Items #8–10 are the
remaining calibration phase, and they are gated on weather, not on code (#7 has since
resolved — see *Closed*, below).

**Everything below is grouped accordingly:** *Closed* is decided and needs no revisiting;
*Field calibration* needs data that only time and storms produce; *Carried into v1.1* is the
short list of real engineering work that is known, scoped, and deliberately not in v1.

---

## Closed

- **#1.** ~~HA install type on mini PC (HAOS/Supervised → add-on path; Container → sidecar docker-compose path).~~ **RESOLVED: HA install is HAOS.** Layer 2 modeling service is built as a local add-on (spec Addendum A).
- **#2.** ~~Google Floods API: does a virtual gauge (hybas) land on the creek, or only on the
  larger receiving reach? What are its thresholds?~~ **CLOSED 2026-09-20.** `app/sources/google_floods.py` searches `gauges:searchGaugesByArea` for
  every gauge Google models within 25 mi — non-quality-verified and virtual HydroBASINS
  gauges included, which are the only plausible candidates on a creek this small — and
  reads `floodStatus:queryLatestFloodStatusByGaugeIds` for the nearest ten. The `Creek
  Google Flood Gauges` sensor is the count, and the add-on log names each gauge with its
  river, distance and verification state on every daily re-discovery. A count of 0
  answers that no gauge, verified or virtual, lands close enough to be a useful proxy
  for this creek.

  The second half — does Google's *flash flood* product (a different, ungauged-basin
  forecast, not a gauge) reach a basin this small — is also answered by the same 0.22.0
  release: `flashFloods:search` plus `serializedPolygons/{id}` polygon geometry are now
  checked against the site's own coordinates every 30 min (`google_flash_flood_likely`,
  `google_flash_flood_highly_likely`, `google_flash_flood_events`; Addendum C 2j). Unlike
  the gauge search, this is a direct read of the site itself, not a neighbouring proxy.

  **Closed rather than carried as a residual.** #2 also asked "what are its thresholds?"
  — the gauge severity ladder (2i) still reports Google's 4-step category, not the
  gauge's own numeric thresholds (`gaugeModels.batchGet` / `gauges.queryGaugeForecasts`).
  With the gauge count at 0, there is no gauge near enough for that residual to apply to
  *this* creek — it would only become relevant if a future daily re-discovery ever finds
  one. If that happens, it's a new open question, not pending work under this one.
- **#3.** ~~NWM reach ID for the creek's segment at the sensor site.~~ **RESOLVED.**
  Identified and verified against `api.water.noaa.gov/nwps/v1/reaches/<id>` — the reach
  reports its own position ~100 m from the sensor site and returns a short-range streamflow
  forecast. The ID itself is site-identifying, so it is set in the add-on's configuration
  rather than committed as a `config.yaml` default.
- **#4.** ~~Which 3–5 upstream PWS stations are reliable (uptime, tipping-bucket quality)?~~
  **RESOLVED (two identified, configured outside the repo).** Two upstream stations are in
  use; their IDs now live in the add-on's own configuration rather than in `config.yaml`,
  since they name the location. Spec §3 calls for 3–5 to average over; with two, one station
  dropping out halves the sample, so adding 1–3 more from the upstream corridor is a
  *nice-to-have*, not a blocker. Revisit if the `Creek Upstream Data Missing` watchdog
  starts firing — that means the stations went offline or left the WU API.
- **#5.** ~~Exact low-water reference datum and surveyed bank height at the sensor site (measure at install).~~
  **RESOLVED 2026-09-12.** Datum is the creekbed, not a low-water surface: it is easier to
  survey precisely, and depth above the bed is the more useful number — it reads as true
  water depth and goes to ~0 on a dry bed rather than negative. Measured creekbed → sensor
  face = 43.5 in = 1105 mm, and that is the `mount_height_mm` default in
  `firmware/esp32_rfm69_gateway/gateway.base.yaml`.
  ~~Sensor sits ~6 in above bank top, so bank top ≈ 37.5 in above the creekbed.~~ **Superseded
  below — that was an eyeball estimate, not a survey.**
  **The §2 mounting geometry is obsolete** — it describes an earlier, abandoned site with a
  higher bank, dropped because it was problematic for solar. This pole is at the property
  low spot; 9-10 ft above bank there is meaningless, and the system's job is warning on
  *creek cresting the bank*, not on a 100-year flood. Do not treat §2's numbers as a target.
  ~~The placeholder thresholds in `tiers.py` survive the datum change nearly intact by
  coincidence: Warning 2.0 ft = 24 in and Emergency 2.5 ft = 30 in both still sit below
  bank top (≈ 3.13 ft), and Emergency is still ≈ "bank top minus 6 in".~~ **Superseded below.**

  **UPDATE 2026-09-14 — bank top actually surveyed, and it changes the picture.** Measured
  creekbed → top of bank at the sensor location = **44.25 in**, not the ~37.5 in eyeballed
  above. Sensor face is still 43.5 in (1105 mm) — **installation height is not changing yet**
  — which puts the sensor face **~0.75 in *below* bank top**, not ~6 in above it as previously
  assumed. That also moves the blanking-zone ceiling (`mount - blanking` = 1105 − 150 = 955 mm
  ≈ 37.6 in, unchanged since mount is unchanged) to **~6.65 in below bank top**, not
  coincident with it — see #14, whose "sensor goes blind almost exactly at overtopping"
  framing was built on the old estimate and needs revisiting; the clamp-not-NaN fix there is
  still correct regardless of the exact gap. `tiers.py`'s "bank top minus 6 in" comment on
  `EMERGENCY_STAGE_FT` is now off by ~8 in for the same reason (see #14, #16). A short pole
  extension to raise the install height is planned this week (#16) — re-measure and update
  `mount_height_mm` once it's in.

- **#6.** ~~WiFi RSSI at the pole via the outdoor AP (bag test before final mount).~~ **RESOLVED 2026-09-20.** With the Moteino + RFM69HW architecture, the relevant test is **RFM69 RSSI** at the pole location. Field deployment 2026-09-19+ shows sustained RSSI around −72 dBm over 24+ hours, well above the −80 dBm target, with effectively 0% packet loss across rainfall events. The link margin is confirmed adequate.

- **#7.** ~~WH51 readings are relative (0–100%) and site-specific. After the next soaking
  rain and a dry stretch, record the empirical "saturated" and "dry" values at each
  burial spot; these calibrate the Tier 0 soil-moisture threshold.~~ **RESOLVED
  2026-09-20.** Both WH51 probes are calibrated at their burial spots — the empirical dry
  and saturated values have been recorded, so `soil_moisture_mean_pct` (`features.py`)
  now reads a meaningful 0–100% at each probe rather than an arbitrary scale. This
  doesn't change anything in code by itself: the app already consumes the WH51 entities'
  percentage as-is, with no normalization step to update. What it unblocks is #8 —
  `ADVISORY_SOIL_PCT` (70%, `tiers.py`) can now be sanity-checked against real dry/wet
  readings instead of an unverified placeholder.

- **#11.** ~~Solar/battery sizing for the creek node.~~ **RESOLVED again 2026-09-25 — the load
  is ~2 mA (#17).** At that load the 6 Ah pack is ~100+ days with no sun, and panel sizing
  stops mattering outside storm days. The hardware is settled: **Adafruit bq24074 linear charger**, a KSD9700 cold-cutoff switch
  upstream of the charger, and a **1S4P pack of 1500 mAh 18650 cells (6 Ah total)** optimized
  for compact pole mounting. Panel wattage is recorded as 6 W here and 7 W throughout
  `docs/node-hardware.md`; **nobody has checked the label** — do that before trusting either
  sizing table.

  *History:* the load figure that first made this "resolved" was wrong. It read "~25 mA
  average, confirmed in field operation 2026-09-19+", but the node has no shunt and nothing
  in the system could confirm a current. The first real measurement, from the pack's own
  overnight discharge on 2026-09-19/20, came to **~60 mA**, which reopened this. #17 then
  found the always-on radar (~36 mA in truth; the 60 was an overestimate) and, once it was
  fixed, measured ~2 mA. See `docs/node-hardware.md`, "Measuring average draw without a
  shunt".

- **#12.** ~~Charger and regulator selection for the creek node (spun out of #11).~~ **RESOLVED —
  deployed with Adafruit bq24074.** Keep Li-ion with the **Adafruit Universal USB / DC / 
  Solar Lithium Ion/Polymer charger (bq24074)** — a linear charger (67 % efficiency 6 V → 
  4 V). It was judged adequate against an assumed ~25 mA draw; the pack is **6 Ah**, not the
  23.2 Ah this entry claimed. **#17 settled it (2026-09-25): the load is ~2 mA, so the
  linear charger stays and the MPPT upgrade is not needed.** The ~60 mA that briefly put it
  back in question was a wiring fault plus an overestimate. Pair with low-voltage
  protection. The 5 V boost's enable pin duty-cycles the radar from a GPIO; the wiring was
  wrong until 2026-09-23 and is verified since.
  Chemistry changes do not solve cold charging and 12 V controllers idle away a fifth of the
  node's budget.

- **#13.** ~~Sub-freezing charge cutoff.~~ **RESOLVED — freezer test passed.** KSD9700 5 °C
  normally-open bimetallic switch in the panel positive line, upstream of the charger.
  The bench freezer test confirmed the switch reads open when cold and closes on warming
  (the failure mode budget sellers sometimes ship inverted), and the reset differential
  came back acceptable — no need to move to the 10 °C variant. See `docs/node-hardware.md`
  for the wiring rationale (switching the panel line rather than the battery line, so a
  failed-open switch only costs charging rather than the load).

- **#15.** ~~Adaptive crest sampling was lost in the Moteino port — the node reports on a
  fixed 60 s cycle, so the crest of a flashy rise is sampled at 60 s.~~ **RESOLVED
  2026-09-20 — implemented, pending deploy.** `main.cpp` now carries its previous reading
  across sleeps, computes its own rate of rise, and drops from 60 s to 5 s on a confirmed
  rise, reverting after a longer quiet period. The 0.02 in/min trigger (0.5 mm/min in the
  radar's own units) is preserved from the retired ESPHome node, and with it the reason
  for that number: it sits deliberately below the add-on's 0.05 in/min
  `WARNING_RATE_OF_RISE_IN_MIN`, so the node is already sampling fast before a Warning is
  plausible.

  Feasible at all because `LowPower.standby()` is a WFI rather than a reset — `loop()`
  resumes in place and plain SRAM statics survive every sleep, where an ESP32 deep-sleep
  node would have needed `RTC_DATA_ATTR` or flash for the same state.

  **Two hazards in the existing code had to be cleared first, both of which only bite at a
  short interval** — which is why neither mattered at a fixed 60 s and both would have
  been easy to ship on top of:

  - `sleepSeconds()` armed `MATCH_HHMMSS` and then slept, and that match fires once per
    *day*. An alarm second slipping past between arming and the WFI therefore cost not
    5 s but **24 hours**, with the node off the air for all of it — including the listen
    window OTA needs to push a fix. It now re-reads the clock as late as possible and
    skips the standby unless the target is still ahead, so the worst case is one un-slept
    cycle. Seconds-of-day arithmetic replaced the hand-rolled h/m/s rollover at the same
    time.
  - Each wake already costs ~2 s (sensor settle + Modbus read + the 1.5 s OTA listen
    window), so a 5 s sleep is really a ~7 s period at ~30 % awake. The OTA window drops
    to 300 ms in fast mode — shortened rather than skipped, so a push stays possible
    mid-rise.

  Noise drove one design choice worth recording: the SEN0676 is ±5 mm, so no single pair
  of samples resolves 0.5 mm/min — the trigger is noise-dominated by construction.
  Raising the threshold would have broken the ordering above, so entering fast mode
  instead requires consecutive qualifying samples, mirroring the add-on's own
  `WARNING_RATE_OF_RISE_CONFIRM_SAMPLES` guard on the same quantity. Leaving takes longer
  than entering, so a plateau part-way up a real rise doesn't drop the cadence back to
  60 s just before the crest.

  **Still to do before this is true at the creek.** The code is on `main` and CI has
  regenerated `firmware.hex`, so it is ready to send: press "Push Node Firmware" on the
  gateway — **not mid-storm**, since the node blocks for the whole transfer and stage
  goes dark with it. Then confirm from stage history that a real rise is actually sampled
  at ~5 s. Until that push happens the node on the pole is still running the fixed 60 s
  firmware, and note that `done` on the OTA status sensor is not by itself proof the
  image took (`firmware/README.md`, "`-DMOTEINO_M0` is load-bearing" and the
  `node never acknowledged EOF` row) — the cadence change in stage history is.

  Two follow-ups are deliberately not in it: surfacing the payload's new `fast` flag as a
  gateway entity, and restoring the regression test that enforced the node/add-on
  threshold ordering, which the port also removed.

- **#16.** ~~Optional: raise the pole 24–36 in.~~ **COMPLETED.** Pole raised. Buys overbank
  depth headroom for model training (un-censoring the rare big events) and reduces the
  debris-impact risk to the sensor at the property low spot.

---

## Field calibration — gated on weather, not on code

These need observations that only time produces. None of them blocks v1; together they *are*
the calibration phase.

- **#8.** Tier thresholds in `rate_of_rise/app/tiers.py` are placeholders. The forecast/rainfall
  ones (Advisory, Watch) can be tuned from the first few storms without the creek gauge;
  the stage-based ones (Warning, Emergency) depend on #5.

  **Reviewed 2026-09-20 — not automatically tuned; these are hand-edited constants.**
  `tiers.py` has no fitting or auto-tuning path: `ADVISORY_QPF_24H_IN`, `ADVISORY_SOIL_PCT`,
  `WATCH_UPSTREAM_3H_IN`, `WARNING_STAGE_FT`, `EMERGENCY_STAGE_FT`, etc. are plain
  module-level constants. The only code that reads them besides `tiers.py` itself is
  `train.py`, and it *consumes* `WARNING_STAGE_FT` / `WARNING_RATE_OF_RISE_IN_MIN` to
  label training rows as danger/no-danger — it does not solve for them or write anything
  back. Tuning any threshold here means reviewing observed storms (dashboard history, or
  the storm log `storms.py` maintains) and hand-editing the constant, then bumping the
  add-on version. #5 (datum) and #7 (soil calibration, below) are both now resolved, so
  the stage-based and soil-moisture-driven constants are unblocked for review whenever
  storm data supports it; the rainfall/QPF/API-index ones still want a few more real
  storms logged first (see #9).

- **#9.** The API recession constant `k` (`app/sources/apindex.py`, currently 0.92 ≈ a two-week
  memory) is a literature default, not a fitted value. Fit it once a few storms are
  recorded — the right `k` is the one whose index best separates storms that produced a
  creek response from those that did not.

  **Reviewed 2026-09-20 — still open; the fitting tool doesn't exist yet.**
  `DEFAULT_K = 0.92` in `apindex.py` is a plain constant with no code path that recomputes
  it from data. `storms.py` logs completed storm events — used today only to gate ML
  retraining (`min_events_for_ml`) — but nothing consumes that log to solve for `k`. The
  data-availability half of this has moved, though: per `docs/project-knowledge.md`, the
  storm log has already cleared the 10-event `min_events_for_ml` gate, so there is likely
  enough logged rainfall history to attempt a fit. What's missing is the fitting step
  itself (grid-search or optimize `k` against which value best separates storms that
  produced a creek response from ones that didn't) — a small tooling task to build, not a
  wait on more storms.
- **#10.** Rain-on-snow thresholds (`app/features.py`: 0.20 in SWE, 34 °F) are placeholders, and
  the flag cannot be validated until a winter rain-on-snow event is actually captured.

- *#11 is Closed, above; its load figure was settled by #17 at ~2 mA, which makes the 80 mA
  analysis below moot for the as-built node.* What follows is the
  original ESP32-C6-era sizing analysis that led to that decision — retained as background
  reasoning, not as a description of the as-built node, which uses a **bq24074 linear
  charger** (not MPPT), a KSD9700 cutoff, and a **6 Ah pack** (not 23.2 Ah). Panel wattage
  is unconfirmed; this file says 6 W and `docs/node-hardware.md` says 7 W.

  Load ~80 mA (1.92 Ah/day) *— original ESP32-C6 estimate.* Scoped to the
  stated flood season (early spring → mid-December), a **7 W panel** covers March–November
  and is marginal only in early December on a linear charger.
  **The binding constraint is recovery, not capacity.** Li-ion cannot be charged below
  0 °C, so a freeze runs on the pack alone — but late-autumn *surplus* (harvest minus
  load) is ~+0.9 Wh/day in November and **negative in early December** at 80 mA on a
  linear charger. A node that goes flat in a December cold snap therefore stays flat
  through January and February and only recovers around March, which is when the season
  reopens and ice-jam / rain-on-snow risk peaks. A bigger pack does not help: it delays
  the crossing and then refills proportionally slower on the same absent surplus.
  Fix the surplus instead: (a) duty-cycle the radar on a switched 5 V rail, ~80 → ~48 mA,
  which alone turns early December positive; (b) MPPT/buck charger rather than linear,
  recovering the third burned going 6 V → 4 V; (c) low-voltage protection on the pack,
  required regardless, or a flat node becomes a scrap pack. With those, **4P–6P is
  plenty**. A deliberate winter shutdown (pull and charge the pack indoors, reinstall in
  February) is a legitimate zero-cost alternative.
  *(Superseded — the Adafruit/bq24074 NTC question doesn't apply to the as-built KSD9700
  cutoff, and the C6 draw estimate is moot now that the node is a Moteino. PVWatts against
  the actual pole, where tree shading will dominate, is still worth doing but isn't
  blocking.)*

### Charger and regulator selection — the reasoning behind #12

  Spun out of #11; the decision itself is recorded under *Closed* above.
  **Chemistry change does not fix cold charging.** LiFePO4 has the same 0 °C charge
  prohibition as Li-ion; NiMH is marginal and brings unreliable −ΔV termination at solar
  currents. Lead-acid genuinely charges to about −20 °C, but a *flat* lead-acid freezes at
  −8 °C and splits its case, it stores less usable energy at 0 °C than the 18650s already
  on hand, and it weighs ~2.5 kg on a guy-wired pole.
  **The controller is the trap (for larger systems).** 12 V MPPT controllers idle at 
  10–18 mA — 12–37 % of a typical 80 mA budget — so going lead-acid would spend a fifth 
  of the power the exercise is meant to save. PWM controllers throw away ~28 % clamping an 
  18 V Vmp panel to 13 V.
  **Deployed decision: Adafruit bq24074 linear charger.** This was decided against an assumed
  ~25 mA draw and a misrecorded 23.2 Ah pack, where the linear charger's ~67 % efficiency
  (vs. MPPT's ~90 %) was not load-bearing. The pack really is 6 Ah, but the load is
  **~2 mA** (#17, measured 2026-09-25), so the charger's efficiency still is not
  load-bearing, and the bq24074 stays. It is simpler and has proven reliable in field
  operation.
  Pair with low-voltage protection on the pack. Choose the 5 V boost with an enable pin — 
  that EN line is the radar load switch, so duty-cycling costs a GPIO and a 100 ms settle 
  rather than a separate MOSFET.
  **OTA over winter:** bring the node indoors with the pack; on USB it stays on WiFi and
  takes updates normally. Winter is when firmware iteration happens anyway.

*(#13 is now Closed, above — the freezer test that this section was waiting on has passed.)*

---

## Carried into v1.1 — known, scoped, deliberately not in v1

- **#17.** ~~**The node draws ~60 mA, and the firmware says it should draw 1–2 mA.**~~
  **RESOLVED 2026-09-25 — it was the radar rail and nothing else. The node now draws
  ~2 mA, which is what the firmware predicts.** Opened 2026-09-20, spun out of #11.

  **Meter at the pole, 2026-09-23.** The radar rail drew a constant **35 mA**, because the
  SHDN wire was on the wrong header pin and the radar ran 24/7. With the radar held on for
  about a minute, the rail averaged **35.09 mA**. The Moteino rail peaked at 12 mA. The pack,
  with the panel disconnected, peaked at 58.7 mA (radar and awake MCU together) and averaged
  **~40 mA**, read off a bouncing meter. Moving the wire to `~4` made the rail switch. It also
  exposed a radar warm-up that was too short, fixed in `main.cpp` ("Radar warm-up") and on
  the pole since the 2026-09-24 19:01 UTC push. A post-fix reading of "10 mA average" on the
  radar line came from a window of a few seconds that caught one pulse; the slope below
  rules out a standing drain of that size.

  **The first clean night after both fixes, 2026-09-25 01:56 → 10:22 UTC,** fit the same way
  as the 2026-09-19/20 night below:

  | Same clock window, 01:56 → 10:22 UTC | Overnight slope |
  | --- | --- |
  | 09-19/20, radar on 24/7 | −10.55 ± 0.06 mV/h |
  | 09-21/22, radar on 24/7 | −10.79 ± 0.06 mV/h |
  | 09-22/23, radar on 24/7 | −10.68 ± 0.06 mV/h |
  | **09-24/25, rail switching** | **−0.51 ± 0.06 mV/h** |

  The drop is about 20×. It holds when the night is split in halves (−0.42 ± 0.18 and
  −0.70 ± 0.17), and with outdoor temperature as a covariate (−0.49 ± 0.21 mV/h,
  temperature term +0.02 ± 0.25 mV/°C across 46 → 34 °F). The pack sat at 4.19 V all
  night, and the 4.2 mV residual matches the earlier nights, so the reading is live and
  dithering.

  - **The meter supplies the anchor `docs/node-hardware.md` said was missing.** The fix
    removed a measured 35.09 mA, less the ~0.3–1 mA the radar still draws while it warms
    up each wake: ~34.4 mA. That is worth 10.16 mV/h, so K ≈ **3.4 mA per mV/h**, with no
    OCV curve involved.
  - **Now: 0.51 × 3.4 ≈ 1.7 mA** (1–2.5 mA across the fit's uncertainty). **Before:
    10.67 × 3.4 ≈ 36 mA**, which agrees with the meter's 35 mA radar plus ~1 mA for the
    rest, and its ~40 mA pack average. The old "~60 mA" came from the OCV-curve guess and
    ran about 1.6× high. The OCV band alone gives 1.9–3.8 mA for last night, which overlaps.
    One assumption: K is taken to be the same at 4.19 V as at 4.0–4.1 V.
  - **Suspect (2) is ruled out.** A SAMD21 kept out of standby (~12 mA) would show about
    3.5 mV/h.
  - **Suspect (3) is bounded.** The firmware's own duty cycle (radar ~0.3–1 mA while it
    warms up, the Moteino rail's 12 mA for ~3 s a wake ≈ 0.6 mA) already comes to
    0.9–1.6 mA. That leaves room for at most ~1 mA of quiescent draw across the boosts,
    charger and protection board.
  - **Consequences:** ~40 mAh/day, under 1 % of the 6 Ah pack. A full pack runs the node
    **~100+ days with no charging at all**, against ~4 days at the old figure. #11 closes
    again, #12's linear charger stays, and the winter-recovery problem in
    `docs/node-hardware.md` does not arise at this load.
  - **Storm days now dominate the budget.** Fast mode holds the radar rail up, so the 35 mA
    returns for as long as the creek is rising: ~0.84 Ah (~14 % of the pack) per 24 h of
    fast sampling.

  The recorder wrote nothing from 2026-09-24 21:00 to 2026-09-25 01:55 UTC, a second stall
  that an HA restart ended. That is why the window starts at 01:56 rather than at dusk.

  *What follows is the investigation as it stood before the resolution, kept because the
  diagnostic machinery it describes is still in the firmware.*

  The first measurement of average draw — least-squares fit to
  the pack's overnight discharge, 2026-09-19/20, −10.87 ± 0.08 mV/h over 265 reports — puts
  the node at **~60 mA** (40–80 mA, the band set by the unknown OCV curve). Summing what the
  firmware actually does gives 1–2 mA. A ~30× gap is a fault, not a modelling error.

  Leading suspects, in order: (1) **the radar's 5 V rail never actually switches** — the
  firmware drives `SENSOR_EN_PIN` correctly every cycle, but it cannot tell whether the
  U1V11F5's SHDN pin is wired to it, and an always-on SEN0676 is ~35 mA on its own;
  (2) `LowPower.standby()` not being entered, leaving the SAMD21 at ~12 mA; (3) unbudgeted
  quiescent draw across the two boosts, the charger and the protection board.

  **Confirmable without a site visit.** `DIAG_RADAR_WINDOW_ENABLE` in `main.cpp` (ships at
  `0`) holds the radar rail off for a 2 h window once a day. If the overnight slope drops
  from ~11 mV/h to ~4–5 mV/h the wiring is good and the load is somewhere else; if it does
  not move at all, SHDN is not connected, because nothing the firmware does to the pin
  reaches the regulator. Two hours is enough — ~120 samples puts the slope standard error
  near 0.7 mV/h against a ~6 mV/h effect. It is also the calibration anchor #17's parent
  problem needs: a known ~35 mA step against a measured slope change converts mV/h to mA
  for every future night.

  **Two runs produced nothing, both diagnosed as results before being checked.** 2026-09-21
  aborted on its first peek every night: the abort guard sat at 10 in of depth while the creek
  baseline is 11-13 in. 2026-09-23 never opened the window at all: it was keyed off
  `secondsOfDay()`, and RTCZero preserves the RTC across the watchdog reset RFM69_OTA reboots
  through, so the offset was anchored to the last battery connect rather than to the flash and
  the 8.4 h run never swept across it. Both times the unchanged battery slope looked exactly
  like "SHDN is disconnected, confirmed". **Check `binary_sensor.*_creek_node_diagnostic_active`
  went on before reading any slope** -- that entity exists because this mistake was made twice.

  **To run it: press "Push Node Diagnostic Firmware" in Home Assistant. That is the whole
  procedure.** CI builds the armed image from the same commit as the normal one and attaches
  both to the same release, so there is nothing to arm, build or convert by hand, and no
  local toolchain involved. `DIAG_RADAR_WINDOW_ENABLE` stays `0` in the source — the workflow
  flips it at build time — and a test asserts that, because a forgotten `1` in the repository
  would blind the creek sensor.

  **Nothing has to be remembered afterwards, and the press does not have to be well timed.**
  The window is one-shot: it latches closed for the life of the boot once it has collected a
  usable sample, so a diagnostic image left installed costs one window, once — not a blind
  window every day until somebody notices. "Usable" means both that the window ran to
  something like half its length *and* that the pack fell across it; a window that landed in
  daylight sees the pack rise (during charging the A5 divider reads the charger's OUT rail,
  so the rise is unmistakable) and simply retries the next day at the same offset. A window
  cut short by high water retries too. So the press can happen at any hour and the node keeps
  trying until it catches a real overnight discharge.

  Press "Push Node Firmware" whenever convenient to go back to the stock image. Nothing
  depends on doing that promptly.

  The window self-protects while it runs: it never opens while the node is in fast-sampling
  mode, it abandons the night if a reading shows the creek up (distance < 850 mm ≈ 10 in of
  depth at the current mount), and it surfaces for one ordinary reading every 20 minutes so
  the longest blind gap is 20 min rather than the full two hours.

  **Cheapest discriminator is still a meter at the pole** — measure pack current with the node
  idle between reports. Failing that, the two-night calibration in `docs/node-hardware.md`
  ("Measuring average draw without a shunt") separates the constant drain from the
  per-wake cost without opening the enclosure, using
  `sensor.creek_gateway_creek_node_packets` and `binary_sensor.creek_gateway_creek_node_fast_sampling`
  (both added 2026-09-20 for this purpose).

  **Consequences if it is real:** runtime falls from ~10 days to ~4, #12's charger decision
  loses most of its margin, and the "duty-cycle the radar" fix that the whole winter-survival
  analysis rests on turns out never to have been in effect. *(It was real, the radar rail
  was the whole of it, and the fix is now in effect; see the resolution above.)*

- **#14.** ~~Sensor goes blind exactly at the alarm condition, and the tier silently
  de-escalates.~~ **RESOLVED 2026-09-12 (the dangerous half).** Usable range tops out at
  `mount - blanking` = 1105 − 150 = 955 mm ≈ 37.6 in, which was believed to be bank top (#5)
  at the time — the sensor stops measuring at almost exactly the depth where the creek comes
  over. The old code published `NaN` there, `features.py` turned that into `stage_ft=None`
  *and* `rate_of_rise_in_min=None`, and `tiers.py:_ge()` returns False for None, so Warning
  and Emergency would have **stopped firing at the moment of overtopping**.
  The fix reframes the reading rather than latching state: a distance *inside* the blanking
  zone is not unknown, it means the water is at least `mount - blanking` deep, so the gateway
  now clamps depth to that ceiling and logs it. Only a distance that is implausibly *far* —
  a genuinely lost target — still publishes NaN. The tier therefore stays where it belongs
  as the creek tops the bank.
  **UPDATE 2026-09-14: the "almost exactly" part no longer holds.** Bank top is actually
  44.25 in (#5, re-measured), not ~37.5 in, so the blanking ceiling (37.6 in, unchanged) sits
  **~6.65 in below** bank top rather than coincident with it. The clamp-not-NaN fix is still
  correct and still necessary, but as currently mounted the sensor goes blind ~6.65 in of
  depth *before* the creek tops the bank, not at the moment it does — there's a blind window
  on the way up, not just at the crest. Raising the install height (#16) closes that gap;
  until then, treat "reading pinned at the blanking ceiling" as "at least bank top minus
  ~6.65 in," not "at the bank."
  **Still open (the residual):** a target lost to debris, foam or turbulence at high flow
  still blanks stage, and nothing holds the tier through it. A hold — "keep the last tier for
  N minutes when stage drops out while it was rising" — needs a state-hold policy and is the
  main reason this is v1.1 rather than v1. The 30-minute `stage_stale` watchdog
  (`health.py`) is the only backstop today.

*(#15 is now Closed, above — adaptive crest sampling is back on the node.)*

*(#16 is now Closed, above — the pole has been raised.)*

*(#2 is now Closed, above — the residual is deliberately not tracked as pending work,
since the gauge count it depends on is 0.)*
