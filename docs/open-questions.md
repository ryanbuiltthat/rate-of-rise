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

So the backlog below is not a list of things that went unfinished. Items #7–10 are the
remaining calibration phase, and they are gated on weather, not on code.

**Everything below is grouped accordingly:** *Closed* is decided and needs no revisiting;
*Field calibration* needs data that only time and storms produce; *Carried into v1.1* is the
short list of real engineering work that is known, scoped, and deliberately not in v1.

---

## Closed

- **#1.** ~~HA install type on mini PC (HAOS/Supervised → add-on path; Container → sidecar docker-compose path).~~ **RESOLVED: HA install is HAOS.** Layer 2 modeling service is built as a local add-on (spec Addendum A).
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

- **#11.** ~~Solar/battery sizing for the creek node.~~ **RESOLVED — as-built and confirmed in field.** Final
  hardware: 6 W solar panel, a CN3791-class 1S MPPT controller, a KSD9700 cold-cutoff
  switch upstream of the charger (the Adafruit bq24074 board and its NTC input, discussed
  below as an alternative, was not used — that verification is now moot), and a 1S4P pack
  of 5800 mAh 18650 cells (23.2 Ah total — roughly double the 12 Ah the original analysis
  budgeted for). The Moteino M0 + RFM69HW draw is **~25 mA average, confirmed in field 
  operation 2026-09-19+** (node reporting every 60 s continuously with stable battery 
  voltage and good RSSI). This pack alone covers weeks with zero recharge at 0 °C, so 
  duty-cycling the radar is no longer load-bearing for winter survival — see the updated 
  numbers under "Pack sizing" in `docs/node-hardware.md`. The original ESP32-C6-era 
  analysis is retained there for reference, not because it still describes the as-built system.

- **#12.** ~~Charger and regulator selection for the creek node (spun out of #11).~~ **RESOLVED —
  decision made; see the full reasoning below under "Charger and regulator selection".**
  Keep Li-ion, replace the linear charger with a CN3791-class 1S MPPT module, pair with a
  4P–6P 18650 pack and low-voltage protection, and choose the 5 V boost with an enable pin
  so the same line duty-cycles the radar. Chemistry changes do not solve cold charging and
  12 V controllers idle away a fifth of the node's budget.

- **#13.** ~~Sub-freezing charge cutoff.~~ **RESOLVED — freezer test passed.** KSD9700 5 °C
  normally-open bimetallic switch in the panel positive line, upstream of the charger.
  The bench freezer test confirmed the switch reads open when cold and closes on warming
  (the failure mode budget sellers sometimes ship inverted), and the reset differential
  came back acceptable — no need to move to the 10 °C variant. See `docs/node-hardware.md`
  for the wiring rationale (switching the panel line rather than the battery line, so a
  failed-open switch only costs charging rather than the load).

- **#16.** ~~Optional: raise the pole 24–36 in.~~ **COMPLETED.** Pole raised. Buys overbank
  depth headroom for model training (un-censoring the rare big events) and reduces the
  debris-impact risk to the sensor at the property low spot.

---

## Field calibration — gated on weather, not on code

These need observations that only time produces. None of them blocks v1; together they *are*
the calibration phase.

- **#7.** WH51 readings are relative (0–100%) and site-specific. After the next soaking rain and a dry stretch, record the empirical "saturated" and "dry" values at each burial spot; these calibrate the Tier 0 soil-moisture threshold.

- **#8.** Tier thresholds in `rate_of_rise/app/tiers.py` are placeholders. The forecast/rainfall
  ones (Advisory, Watch) can be tuned from the first few storms without the creek gauge;
  the stage-based ones (Warning, Emergency) depend on #5.

- **#9.** The API recession constant `k` (`app/sources/apindex.py`, currently 0.92 ≈ a two-week
  memory) is a literature default, not a fitted value. Fit it once a few storms are
  recorded — the right `k` is the one whose index best separates storms that produced a
  creek response from those that did not.
- **#10.** Rain-on-snow thresholds (`app/features.py`: 0.20 in SWE, 34 °F) are placeholders, and
  the flag cannot be validated until a winter rain-on-snow event is actually captured.

- *#11 is now Closed, above.* What follows is the original ESP32-C6-era sizing analysis
  that led to that decision — retained as background reasoning, not as a description of
  the as-built node (which uses a 6 W panel, MPPT, a KSD9700 cutoff, and a 23.2 Ah pack).

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
  **The controller is the trap.** 12 V MPPT controllers idle at 10–18 mA — 12–37 % of this
  node's budget — so going lead-acid would spend a fifth of the power the exercise is
  meant to save. PWM controllers throw away ~28 % clamping an 18 V Vmp panel to 13 V.
  **Decision: keep Li-ion, replace the charger.** A CN3791-class 1S MPPT module (~0.5 mA
  idle) recovers the third the linear bq24074 burns going 6 V → 4 V and closes the
  December gap. Pair with 4P–6P 18650 and low-voltage protection. Choose the 5 V boost
  with an enable pin — that EN line is the radar load switch, so duty-cycling costs a GPIO
  and a 100 ms settle rather than a separate MOSFET.
  **OTA over winter:** bring the node indoors with the pack; on USB it stays on WiFi and
  takes updates normally. Winter is when firmware iteration happens anyway.

*(#13 is now Closed, above — the freezer test that this section was waiting on has passed.)*

---

## Carried into v1.1 — known, scoped, deliberately not in v1

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

- **#15.** **Adaptive crest sampling was lost in the Moteino port.** Spec §2 asks for 30–60 s
  normally and 5–10 s once the creek is rising, and the retired ESPHome node did exactly
  that — it dropped to 5 s when its own rate-of-rise crossed 0.02 in/min, deliberately below
  the add-on's 0.05 in/min Warning trigger, so the node was already sampling fast before a
  warning was plausible. `firmware/moteino_creek_node/src/main.cpp` now reports on a fixed
  60 s cycle and deep-sleeps between reports, so **the crest of a flashy rise is sampled at
  60 s**. In a basin where rainfall-to-crest is tens of minutes, that is the difference
  between measuring the peak and inferring it. Re-implementing it on the node is cheap in
  code and costs battery only during an actual rise, which is exactly when spending it is
  correct. This also silently removed the test that used to enforce the node/add-on
  threshold ordering.

*(#16 is now Closed, above — the pole has been raised.)*

- **#2.** Google Floods API: does a virtual gauge (hybas) land on the creek, or only on the
  larger receiving reach? What are its thresholds? **Answered as of 0.22.0, with a
  residual.** `app/sources/google_floods.py` searches `gauges:searchGaugesByArea` for
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

  **Residual:** #2 also asked "what are its thresholds?" — the gauge severity ladder
  (2i) is still Google's 4-step category, not the gauge's own numeric thresholds.
  `gaugeModels.batchGet` thresholds and `gauges.queryGaugeForecasts` values remain not
  ingested (see `creek-flood-warning-spec.md`'s 2i entry) — worth doing only once a
  gauge near enough to matter is known to exist, which 2i's own gauge count has now
  confirmed one way or the other.
