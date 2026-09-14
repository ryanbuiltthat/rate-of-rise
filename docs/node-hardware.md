# Creek node — radar sensor and power reference

The SEN0676 register map and the node's power/solar/cold-weather analysis. Both outlived the
ESP32-C6 node this file was originally written for: the radar and the battery are unchanged,
only the MCU and the radio link were replaced.

**Wiring, build, flash, OTA and bench test are not here** — `firmware/README.md` is the
authority for all of those, for both the Moteino M0 node and the ESP32-C3 gateway.

> **The register map below is transcribed from the DFRobot datasheet**, not verified against
> hardware. `firmware/README.md` has the bench-test procedure; step 2 there is the Modbus
> check that confirms it.

## SEN0676 Modbus registers

From the datasheet (`SEN0676_..._datasheet_V1.0.pdf`). Modbus RTU, 8N1, CRC16 (poly
`A001`), read `0x03`, write single `0x06`, default slave address `1`, default baud 115200.

| Register | Access | Meaning | Unit |
|---|---|---|---|
| `0x0001` | R | "Empty height" — radar face → water surface | mm, filtered |
| `0x0003` | R | Water level = installation height − empty height | mm, filtered |
| `0x0005` | R/W | Installation height (radar → channel bottom) | **cm** |
| `0x03F4` | R/W | Device address | `0x01`–`0xFD` |
| `0x03F6` | R/W | Baud rate ÷ 100 (`0x60` = 9600) | — |
| `0x07D4` | R/W | Max range, default `0x0A` | m |

**The firmware reads `0x0001`, not the ready-made water level in `0x0003`.** Three reasons:

1. `0x0003` is derived from the installation height in `0x0005`, which is stored in
   **centimetres** — that would quantise a ±5 mm sensor to 1 cm, throwing away most of the
   precision the SEN0676 was bought for.
2. Keeping the conversion off the sensor means the datum is a Home Assistant number,
   re-settable if the pole is ever moved, with no reflash and nothing written to the
   sensor's flash.
3. `0x0001` is a raw measurement. If depth ever looks wrong,
   `sensor.creek_gateway_sensor_distance` shows what the sensor actually returned.

The default 10 m range (`0x07D4`) needs no change: mounted 43.5 in up, the distance to water
runs from ~0.15 m (bank full) to ~1.1 m (dry bed) — the whole span sits in the near field.

## The datum

`number.creek_gateway_installation_height` is the distance in **mm** from the radar face down
to the creekbed. Depth is then `installation_height − distance`, published by the gateway as
`sensor.creek_gateway_stage` (ft, what `app/tiers.py` thresholds against) and
`sensor.creek_gateway_creek_depth` (in, the readable one).

Surveyed 2026-09-12 at **1105 mm (43.5 in)**, which puts the sensor ~6 in above bank top. The
number is `restore_value: true`, so it survives reboots and reflashes; if the pole is ever
moved, re-measure face-to-bed and set the number — nothing else needs to change.

Because the datum is the bed rather than a low-water surface, depth reads as true water depth
and reads ~0 when the creek is dry, not negative.

## Power budget

Worked per the EE skill's §4.1 method, sized for the **early-spring to mid-December**
flood season rather than year-round.

**This budget is the retired ESP32-C6 WiFi node's, and it is pessimistic for the hardware
actually on the pole.** The Moteino M0 + RFM69HW that replaced it draws far less than a
C6 holding up WiFi — roughly ~25 mA average rather than ~80 mA (open question #11), which
is a different and much easier budget. The C6 numbers are kept because the panel and pack
were sized against them, so they are the conservative case the installed system beats; the
sizing conclusions below therefore still hold, with margin. **Neither figure is measured** —
open question #11 wants the Moteino's real draw on the bench.

| | |
|---|---|
| SEN0676 | 30 mA (datasheet), ~35 mA from the cell through an 85 %-efficient boost |
| ~~ESP32-C6~~ *(retired)* | ~45 mA, WiFi up with `power_save_mode: LIGHT` — **estimate** |
| **Average, as sized** | **~80 mA** → **1.92 Ah/day = 7.1 Wh/day** at 3.7 V |
| Moteino M0 + RFM69HW *(installed)* | ~25 mA total → ~0.6 Ah/day — **estimate, unmeasured** |

### A 7 W panel covers the season

Assuming 0.75 derate (dirt, angle, temperature, non-STC light) → 5.25 W effective.

| Charger | Efficiency | Needs |
|---|---|---|
| Linear (bq24074-class, 6 V panel → 4 V cell) | ~67 % | **2.0 peak-sun-hours/day** |
| MPPT / buck | ~90 % | **1.5 peak-sun-hours/day** |

Approximate a mid-latitude site peak-sun-hours on a steep, winter-friendly tilt — **check
PVWatts for the actual site before committing**, since tree shading at a creekside pole
will matter more than any of this:

| | Mar | Apr–Aug | Sep | Oct | Nov | early Dec |
|---|---|---|---|---|---|---|
| PSH | 3.3 | 4.0–4.8 | 3.9 | 3.0 | 2.3 | 1.9 |
| Linear charger | ok | ok | ok | ok | ok | **short** |
| MPPT | ok | ok | ok | ok | ok | ok |

So 7 W is comfortable March through November and marginal in the first half of December,
*only* on a linear charger. **The charger topology is worth more than another 2 W of
panel** — a linear charger burns the panel-to-cell voltage difference as heat, and at
6 V → 4 V that is a third of the harvest. An MPPT or buck charger removes the December
gap outright.

### Sub-freezing charging, and why the pack size is not the answer

**Lithium-ion must not be charged below 0 °C.** It plates metallic lithium on the anode:
permanent capacity loss, then internal shorts. A damage-and-safety limit, not a derating.
Use the charger's NTC input (the bq24074 has one — verify the Adafruit board exposes it
rather than tying it off) with a 10 kΩ thermistor bonded to a **cell body**, mid-pack, not
floating in enclosure air, which reads above freezing long before the cells do.
Discharging cold is fine to about −20 °C; only charging is prohibited.

So during a hard freeze the node runs on the pack alone. The obvious response is a huge
pack, and it is the wrong one.

**The problem is not the freeze, it is the recovery.** Surplus — what is left to refill a
depleted pack after the load is served — is nearly zero in late autumn:

| | Charger | Load | Surplus | Refill 6P from empty |
|---|---|---|---|---|
| Nov | linear | 80 mA | +0.9 Wh/day | **70 days** |
| early Dec | linear | 80 mA | **−0.5 Wh/day** | **never** |
| early Dec | MPPT | 80 mA | +1.9 Wh/day | 36 days |
| Nov | linear | **48 mA** | +3.8 Wh/day | 18 days |
| early Dec | MPPT | **48 mA** | +4.7 Wh/day | 14 days |

A node that goes flat in a December cold snap does not come back when the creek thaws. At
an 80 mA load on a linear charger it is still flat in January, still flat in February, and
only climbing out around March — **which is exactly when the season reopens, and when
ice-jam and rain-on-snow risk peak.** Rain on snow is the major regional flood driver per
spec §1, and an ice-jammed channel floods harder than an open one, so the tail of that
outage lands on the highest-risk weeks of the year rather than the emptiest.

That is the real cost of dying in winter — not the frozen days, which genuinely do not
matter much, but the months of dead recovery afterwards. (The radar itself is not blind on
ice: it measures distance to whatever surface is there.)

**A bigger pack does not fix this.** It postpones the crossing and then takes proportionally
longer to refill on the same non-existent surplus. What fixes it is making the surplus
real:

1. **Duty-cycle the radar on a switched 5 V rail.** ~80 mA → ~48 mA. This is the change
   that turns early December from a net drain into a genuine surplus, and it is worth more
   than any amount of pack. Needs a load switch, a GPIO, and a settle delay before the
   Modbus read (datasheet: 100 ms startup). Not implemented — no hardware to test against.
2. **MPPT or buck charger** instead of linear. Recovers the third of the harvest a linear
   charger burns going 6 V → 4 V.
3. **Low-voltage protection on the pack — required either way.** Without a cutoff the C6
   will drag cells into deep discharge and ruin them, which converts "node is down" into
   "pack is scrap, discovered in March." With one, going flat is survivable.

### Selected power parts

Settled after working the budget; see open questions #11–12 for the reasoning.

| Role | Part | Why |
|---|---|---|
| Panel | 6 V, 7 W | Covers Mar–Nov; only early Dec is marginal |
| Charger | CN3791-class 1S MPPT, **or** Waveshare Solar Power Manager **only if the `<2 mA` variant** | MPPT beats the linear bq24074's 67 %. Waveshare adds over-discharge protection but is spec'd at 78 % and some variants idle at 30–80 mA, which exceeds this node's whole budget |
| Pack | 4P–6P 18650, 1S | On hand; more usable energy at 0 °C than an SLA twice the weight |
| Pack protection | 1S protection board (over-discharge / over-current) | Separates "node down" from "pack scrap". **Cell to B+/B− only; charger *and* loads both to P+/P−** — the MOSFETs sit between B− and P−, so a charger on B+/B− bypasses over-charge and over-current entirely |
| Radar rail | **Pololu U1V11F5** (5 V step-up, product 2562) | **True shutdown**: SHDN low disconnects the load rather than leaking input through, so it *is* the duty-cycle switch. <100 µA off, <1 mA running |
| C6 rail | **Pololu U1V11F3** (3.3 V step-up, product 2561) | Boosts below 3.3 V and linearly down-regulates above, so it holds 3.3 V across the whole 1S range |

**Two independent rails off the pack**, not one 5 V rail feeding both — the C6 must stay
awake to turn the radar back on, so it cannot sit downstream of the radar's switch.

Feeding the devkit 5 V and letting its onboard LDO drop to 3.3 V is ~56 % end-to-end
(72 mA from the pack for a 45 mA load). Driving the 3V3 pin from a U1V11F3 is 79–89 %
(45–51 mA). That is ~27 mA of pure heat avoided on the budget's largest single load.

**Not a fit, checked and rejected:** Pololu U5Z6F12 / TI UCC33420 (product 5759). It is a
*galvanically isolated* 12 V step-up — 4.5–5.5 V input (a 1S pack never reaches it), ~50 %
efficiency by Pololu's own spec, and quiescent that rises to ~100 mA below its input range,
which is exactly where a Li-ion pack sits. The 820 V isolation is for breaking ground loops;
here the radar shares a ground with the C6 two inches away.

### Sub-freezing charge cutoff

**KSD9700 bimetallic thermal switch, 5 °C, normally-open**, in the **panel positive line**
upstream of the charger. Chosen over an NTC-plus-comparator because it draws nothing at
all and has no electronics to fail; chosen over a charger with a JEITA/NTC input because
the CN3791 appears not to have one.

Switching the panel rather than the battery line leaves the discharge path untouched, so a
failed-open switch costs charging — visible as a declining battery in HA — rather than
killing the load.

5 °C rather than 0 °C is deliberate: it covers the lag between enclosure air and cell
temperature plus the switch's own tolerance.

**Bench-test before wiring — two things, one of them safety-critical:**

1. **Direction.** Standard KSD9700 N/O closes on *rising* temperature, so 5 °C N/O should
   be open below 5 °C and closed above. Budget sellers label low-temperature variants
   inconsistently, and inverted is worse than absent — a circuit that charges *only* when
   freezing. Multimeter on continuity, switch in the freezer: cold must read **open**;
   warming in the hand must **close** it with a click.
2. **Reset differential.** Unpublished, and it runs 5–15 °C across KSD9700 parts. A 10 °C
   differential means a switch that closed at 5 °C stays closed down to −5 °C on the way
   back down — permitting exactly what it was installed to prevent. Same test: note the
   temperature it closes at while warming, and the temperature it opens at while cooling.
   If it opens below 0 °C, move to the 10 °C variant.

   Mitigating, so measure but do not panic: plating risk scales with charge *current*, and
   the window where the differential bites — cold, falling, low sun angle — is when the
   panel is delivering almost nothing anyway. Trickle below freezing is far gentler than
   bulk charging there.

**Mounting:** bond the flat metal face to a **cell body, mid-pack**, with thermal tape or
paste. Enclosure air reads above freezing hours before the cells do. The 5 A / 250 V rating
is vast against ~1.2 A at 7 V, and there is no DC arcing concern at that voltage.

### Chemistry: does another battery type solve the cold-charge problem?

| | Charges below 0 °C? | Verdict |
|---|---|---|
| Li-ion (18650) | **No** — lithium plating | Current plan; needs NTC cutoff |
| **LiPo (pouch)** | **No** — identical chemistry | **Same cell, different packaging.** "LiPo" is the pouch and polymer separator, not a different electrochemistry: same 3.7/4.2 V, same CC/CV profile, same 0 °C limit. For a pole in freeze-thaw the 18650's steel can is the more robust choice, and it is what the spot welder takes. (This is also why the existing "LiPoly" charger already charges 18650s correctly — the reason to replace it is linear-vs-MPPT, not chemistry.) |
| **LiFePO4** | **No** — same 0 °C limit | **Does not help.** The common assumption that LFP fixes this is wrong; it buys cycle life and safety, not cold charging |
| **NiMH** | Marginally — most datasheets also say 0–45 °C, some allow C/20 trickle lower | **Not worth it.** Tolerates trickle overcharge, which suits solar, but −ΔV termination is unreliable at solar currents, self-discharge is worse, and the cell stack voltage is awkward |
| **Lead-acid (AGM)** | **Yes** — to about −20 °C with temperature-compensated voltage | **Genuinely solves it**, with two catches below |

**Lead-acid's own winter trap:** a *discharged* lead-acid battery freezes. Electrolyte
freeze point tracks state of charge — −24 °C at the 50 % DoD floor, but **−8 °C when flat,
at which point the case splits.** So it still needs low-voltage disconnect, and its failure
mode is worse than Li-ion's: a ruined battery and spilled acid rather than a degraded pack.
It is also ~2.5 kg for *less* usable energy than the 18650s already on hand:

| Pack | Usable @ 0 °C | Days @ 80 mA | Days @ 48 mA |
|---|---|---|---|
| 18650 6P (18 Ah) | 67 Wh | 9.4 | 15.5 |
| SLA 12V 7Ah | 34 Wh | 4.7 | 7.8 |
| SLA 12V 12Ah | 58 Wh | 8.1 | 13.4 |

### The controller is the real trap, not the chemistry

Going lead-acid means adopting a 12 V charge controller, and at this scale their idle draw
is not a rounding error:

| Controller | Quiescent | Share of an 80 mA budget | Of a 48 mA budget |
|---|---|---|---|
| CN3791 1S Li MPPT module | ~0.5 mA | 0.6 % | 1.0 % |
| Genasun GV-4 | ~1 mA | 1.2 % | 2.1 % |
| Victron SmartSolar 75/10 | ~10 mA | **12.5 %** | **20.8 %** |
| EPEver Tracer AN | ~18 mA | **22.5 %** | **37.5 %** |

A good 12 V controller would eat a fifth of the power the whole exercise is trying to save.
**Also avoid PWM controllers**: with a nominal-12 V panel (Vmp ~18 V) clamped to a 13 V
battery they throw away ~28 %, which is the same mistake as the linear charger.

### Recommendation: keep Li-ion, change the charger

The chemistry is not the problem. The charger is.

1. **Replace the linear bq24074 with a CN3791-class 1S MPPT** (6 V panel input). Recovers
   the third burned going 6 V → 4 V, closes the December gap, and draws ~0.5 mA idle.
   **Check the connectors before wiring.** On most CN3791 boards the two JSTs are
   solar-in and battery-out, not two panel inputs — a panel into the battery connector
   destroys the module. Confirm with the silkscreen, or meter them: the battery JST reads
   pack voltage with the panel unplugged.
   **One panel is enough.** 7 W with MPPT already covers the season, so a second adds area
   the budget does not need. The one case that justifies two is *shading diversity* — a
   creekside pole under tree cover is a partial-shade site, and shade moves across the
   day rather than scaling with area, so two panels aimed differently (SE/SW) beat one
   larger panel aimed one way. Side by side facing the same direction is strictly worse
   than a single panel of the same total area. If paralleling: identical panels (one MPPT
   input finds one operating point, wrong for both if mismatched), a Schottky blocking
   diode per panel (otherwise a shaded panel loads the lit one), and check the board's
   sense-resistor charge limit against the combined current.
   Decide this from data, not up front: the node reports battery voltage and level, so a
   couple of weeks of logs will show whether the site is shade-limited or area-limited.
2. **4P–6P of 18650** — free, already on hand, and more usable energy than an SLA twice its
   weight on a guy-wired pole.
3. **Low-voltage protection.** Required for either chemistry.
4. **Pick the 5 V boost with an enable pin.** That EN line *is* the radar load switch —
   duty-cycling then costs a GPIO and a 100 ms settle, with no separate MOSFET. Choose a
   boost with genuine shutdown (µA-level) rather than one that idles at mA.

### OTA over winter is a solved problem

The one real objection to pulling the pack in December is losing OTA. It costs nothing to
fix: **bring the node indoors with the pack.** On USB at a desk it stays on WiFi, takes OTA
updates normally, and is available for bench work — and winter is when firmware iteration
would happen anyway, since there is nothing to measure on a frozen creek. Reinstall in
February with current firmware.

That makes the winter shutdown the cheap path and unattended winter operation an optional
upgrade, rather than the other way round.

### Pack sizing (1S × P, ~3000 mAh cells)

With the load duty-cycled, **4P–6P is plenty** — the earlier 8P–10P recommendation was
compensating for a surplus problem that pack size cannot solve.

| Pack | Capacity | @ 20 °C | @ 0 °C, 80 mA | @ 0 °C, 48 mA |
|---|---|---|---|---|
| 4P | 12 Ah | 5.0 days | 3.8 days | 6.3 days |
| 6P | 18 Ah | 7.5 days | 5.6 days | 9.4 days |

**The zero-cost alternative:** given the season genuinely ends mid-December, a planned
winter shutdown is legitimate — pull the pack in December, charge it indoors, reinstall in
February. It sidesteps the recovery problem entirely and needs no new hardware. It only
works if it is deliberate, because the failure mode of *forgetting* is the March outage
above.

### If you build the pack

- **Match and pre-balance cells** to within ~0.05 V before welding. In parallel a
  mismatched cell is charged by its neighbours through the nickel — spot-welding makes that
  uncontrolled current path permanent.
- **Fuse each cell** to the bus with a narrowed link, so one internal short does not have
  the others dumping into it.
- **Capacity-test salvaged cells.** A parallel pack is only as good as its worst member.

Deep sleep is still excluded. A flood-warning node asleep during the rise is not a
flood-warning node.

Tracked as open question #11.
