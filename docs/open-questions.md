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

So the backlog below is not a list of things that went unfinished. Items 7-13 are the
calibration phase, and they are gated on weather and on one freezer test, not on code.

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
  Sensor sits ~6 in above bank top, so bank top ≈ 37.5 in above the creekbed.
  **The §2 mounting geometry is obsolete** — it describes an earlier, abandoned site with a
  higher bank, dropped because it was problematic for solar. This pole is at the property
  low spot; 9-10 ft above bank there is meaningless, and the system's job is warning on
  *creek cresting the bank*, not on a 100-year flood. Do not treat §2's numbers as a target.
  The placeholder thresholds in `tiers.py` survive the datum change nearly intact by
  coincidence: Warning 2.0 ft = 24 in and Emergency 2.5 ft = 30 in both still sit below
  bank top (≈ 3.13 ft), and Emergency is still ≈ "bank top minus 6 in".

- **#6.** ~~WiFi RSSI at the pole via the outdoor AP (bag test before final mount).~~ **UPDATED:** With the Moteino + RFM69HW architecture, the relevant test is now **RFM69 RSSI** at the pole location. The node is mounted and reporting, so the link works; what is still worth doing is logging `sensor.creek_gateway_creek_node_rssi` over 24 h to confirm margin. Target: sustained RSSI better than −80 dBm with < 1% packet loss. WiFi is no longer in the link path for the creek node (the gateway handles WiFi at the house).

- **#12.** ~~Charger and regulator selection for the creek node (spun out of #11).~~ **RESOLVED —
  decision made; see the full reasoning below under "Charger and regulator selection".**
  Keep Li-ion, replace the linear charger with a CN3791-class 1S MPPT module, pair with a
  4P–6P 18650 pack and low-voltage protection, and choose the 5 V boost with an enable pin
  so the same line duty-cycles the radar. Chemistry changes do not solve cold charging and
  12 V controllers idle away a fifth of the node's budget.

---

## Field calibration — gated on weather, not on code

These need observations that only time produces. None of them blocks v1; together they *are*
the calibration phase.

- **#7.** WH51 readings are relative (0–100%) and site-specific. After the next soaking rain and a dry stretch, record the empirical "saturated" and "dry" values at each burial spot; these calibrate the Tier 0 soil-moisture threshold.

- **#8.** Tier thresholds in `creek_modeling/app/tiers.py` are placeholders. The forecast/rainfall
  ones (Advisory, Watch) can be tuned from the first few storms without the creek gauge;
  the stage-based ones (Warning, Emergency) depend on #5.

- **#9.** The API recession constant `k` (`app/sources/apindex.py`, currently 0.92 ≈ a two-week
  memory) is a literature default, not a fitted value. Fit it once a few storms are
  recorded — the right `k` is the one whose index best separates storms that produced a
  creek response from those that did not.
- **#10.** Rain-on-snow thresholds (`app/features.py`: 0.20 in SWE, 34 °F) are placeholders, and
  the flag cannot be validated until a winter rain-on-snow event is actually captured.

- **#11.** Solar/battery sizing for the creek node. ~~Load ~80 mA (1.92 Ah/day)~~ **UPDATED:**
  With Moteino M0 + RFM69HW replacing ESP32-C6 + WiFi, average draw drops to ~25 mA
  (0.6 Ah/day), which significantly eases the solar budget — a 6 W panel covers the
  load with margin through November. The December surplus gap is narrower and may close
  entirely with MPPT + duty-cycled radar. Original analysis (retained below) assumed
  ~80 mA; revise once real current draw is measured on the Moteino bench test.
  Load ~80 mA (1.92 Ah/day) ← *original ESP32-C6 estimate, retained for reference.* Scoped to the
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
  **Still open:** confirm the Adafruit board exposes the bq24074 NTC input; measure the
  C6's real draw (an estimate, ~56 % of the budget); PVWatts the actual pole, where tree
  shading will dominate. See `docs/node-hardware.md`.

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

- **#13.** Sub-freezing charge cutoff — **approach settled, two measurements outstanding.**
  A KSD9700 5 °C normally-open bimetallic switch in the panel positive line, upstream of
  the charger. Zero quiescent, no electronics, and switching the panel rather than the
  battery keeps the discharge path intact so a failed-open switch only costs charging.
  Deliberately not done with the ESP32: the MCU is powered by the pack it would protect,
  so fail-open leaves a cold pack charging and fail-closed means a flat pack can never
  recover — the MCU cannot boot to enable the charging that would let it boot.
  **Outstanding:** (a) confirm the switch opens when cold and closes when warm — budget
  sellers mislabel low-temperature N/O parts, and inverted is worse than absent; (b)
  measure the reset differential, unpublished and 5–15 °C across KSD9700 parts, since a
  10 °C differential would hold the contacts closed to −5 °C after closing at 5 °C. Move
  to the 10 °C variant if it opens below 0 °C. Both are one freezer test with a
  multimeter. See `docs/node-hardware.md`.

---

## Carried into v1.1 — known, scoped, deliberately not in v1

- **#14.** ~~Sensor goes blind exactly at the alarm condition, and the tier silently
  de-escalates.~~ **RESOLVED 2026-09-12 (the dangerous half).** Usable range tops out at
  `mount - blanking` = 1105 − 150 = 955 mm ≈ 37.6 in, which is bank top (#5) — the sensor
  stops measuring at almost exactly the depth where the creek comes over. The old code
  published `NaN` there, `features.py` turned that into `stage_ft=None` *and*
  `rate_of_rise_in_min=None`, and `tiers.py:_ge()` returns False for None, so Warning and
  Emergency would have **stopped firing at the moment of overtopping**.
  The fix reframes the reading rather than latching state: a distance *inside* the blanking
  zone is not unknown, it means the water is at least `mount - blanking` deep, so the gateway
  now clamps depth to that ceiling and logs it. Only a distance that is implausibly *far* —
  a genuinely lost target — still publishes NaN. The tier therefore stays where it belongs
  as the creek tops the bank.
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

- **#16.** **Optional: raise the pole 24–36 in.** Does *not* change when the alarm fires, so
  it is not a warning-capability fix. It buys measurable overbank depth (2–3 ft, which
  un-censors the rare big events for model training) and, more to the point, physical
  survival — at 6 in above bank, debris in overbank flow at the property low spot is a real
  threat to the sensor. Needs a coupler, a few feet of pipe, and possibly a lateral tie to
  the bank.

- **#2.** Google Floods API: does a virtual gauge (hybas) land on the creek, or only on the
  larger receiving reach? What are its thresholds? `google_floods_api_key` exists as an
  option but no source module is built. Lowest priority of anything here — eleven sources
  already feed the model, and this one may well have no gauge near enough to be useful.
