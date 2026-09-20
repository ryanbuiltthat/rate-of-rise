# Rate of Rise

[![tests](https://github.com/ryanbuiltthat/rate-of-rise/actions/workflows/tests.yml/badge.svg)](https://github.com/ryanbuiltthat/rate-of-rise/actions/workflows/tests.yml)

**Warning arrives before the water does.**

A solar-powered radar gauge on a creek with no official stream gauge, fused with twelve
live data sources into a flood-probability model — a DIY flood early-warning system for a
small, flashy creek basin in Lackawanna County, northeastern Pennsylvania. Built on Home
Assistant, a Moteino M0 radio-linked stream gauge, and predictive flood-probability
modeling. The modeling/alerting half ships as the **Rate of Rise** Home Assistant add-on
(`rate_of_rise/`), which gives the whole project its name.

> **Site details are deliberately generalized** throughout this repo — exact coordinates,
> creek and place names, and station IDs. Anything site-specific is supplied through the
> add-on's own configuration, which lives in Home Assistant rather than here.

| | |
|---|---|
| **12** | Live data sources |
| **49** | Features per inference |
| **±5 mm** | Gauge precision |
| **60 s** | Telemetry cadence |
| **250+** | Automated tests, CI on every push |

## The gap this fills

There is no USGS gauge on this creek. The nearest instruments that publish continuous
readings are off-basin or downstream — useful for validating a lag estimate, useless as a
stand-in for how high the water is right now.

It is also a **flashy basin**: roughly 18 mi², and rainfall-to-crest is measured in tens of
minutes, not hours. That short fuse is the entire design constraint. A system that reports a
flood is a logger. This one is built to say *a flood is becoming likely*, early enough that
the answer is still to move things rather than to bail them out.

## How it works

1. **Instrument** — An 80 GHz FMCW radar (DFRobot SEN0676) on a creekside pole measures the
   water surface to ±5 mm over Modbus RTU into a Moteino M0, which transmits on an
   encrypted 915 MHz point-to-point link to an ESP32 gateway at the house — no WiFi at the
   creek, no cellular, no subscription. Solar-powered, ~25 mA average draw. The gateway
   converts raw distance into depth above the creekbed and publishes it to Home Assistant.
2. **Ingest** — The `rate_of_rise` add-on polls twelve sources every fast loop — upstream
   rain gauges, NWS/NOAA forecasts, WPC excessive-rainfall outlooks, NEXRAD storm-cell
   tracks, SNODAS snowpack, National Water Model reach forecasts, USGS reference gauges,
   Google Flood Forecasting, and on-site rain, temperature and soil-moisture probes — into
   49 features per inference.
3. **Correlate & predict** — The add-on builds a nightly dataset of storm events and fits
   the rainfall→response relationship for this specific basin, starting with threshold
   rules and graduating to a trained model that outputs flood probability with lead time,
   saying which of the two produced the number on screen.
4. **Alert** — A five-level tier ladder (All-clear / Advisory / Watch / Warning /
   Emergency) drives phone pushes, escalating to a critical alarm-stream notification that
   sounds through silent and Do Not Disturb. Any official NWS flood product for the area
   floors the tier independently of the model.

## Measured against the bed, not a guess

As-built geometry. Datum is the creekbed itself, so depth reads as true water depth and
falls to zero on a dry bed rather than going negative. Sensor face was surveyed 2026-09-12;
bank top was re-measured 2026-09-14 and turned out ~6.75 in higher than the earlier eyeball
estimate — the sensor now sits **just below** bank top rather than above it, so its blanking
zone begins ~6.65 in of depth *before* the creek tops the bank, not almost exactly at that
point. Readings inside the blanking zone are still clamped to the ceiling, never blanked, so
the alarm cannot quietly stand down — but the clamp now kicks in before overtopping rather
than at it. A short pole extension to close that gap is planned; installation height is not
changing until it's in.

| | Height above creekbed |
|---|---|
| Sensor face | **43.5 in** (1105 mm) |
| Sensor range ceiling (blanking zone) | 37.6 in |
| Bank top (measured 2026-09-14) | **44.25 in** |
| Emergency threshold | 30 in |
| Warning threshold | 24 in |

## What it watches

12 sources · 7 external APIs · dozens of Home Assistant entities, all auto-provisioned by
the add-on via MQTT discovery:

| Source | Gives |
|---|---|
| Creek radar gauge (on-site) | Stage & rate of rise, 60 s |
| Soil moisture ×2 (on-site) | Antecedent wetness, ponding flag |
| Weather station (on-site) | Rain intensity, temperature |
| NWS forecast (QPF) | 6 h and 24 h expected rainfall |
| NWS active alerts | Flood & flash-flood products |
| National Water Model | Short-range reach streamflow |
| WPC Excessive Rainfall Outlook | Rain graded against flash-flood guidance |
| NEXRAD storm-cell tracks | Bearing, speed, dBZ, ETA to site |
| SNODAS snowpack | Rain-on-snow melt contribution |
| USGS reference gauges | Off-basin lag validation |
| Upstream PWS (Weather Underground) | Neighbor rain gauges, drains toward site |
| Google Flood Forecasting | Forecast severity/trend at the nearest gauges Google models, plus flash-flood risk polygons at this location |

## Five tiers, and one that wakes you

Escalation ladder — each rung is the lowest condition that reaches it.

| Tier | Name | Condition |
|---|---|---|
| 0 | All-clear | No elevated risk. Nothing fires. |
| 1 | Advisory | Forecast rain onto already-wet ground, or a WPC excessive-rain risk. |
| 2 | Watch | Rain measured upstream, or radar cells inbound. **Critical push from here up.** |
| 3 | Warning | The creek is answering — 24 in stage, or rising 0.05 in/min. |
| 4 | Emergency | 30 in and climbing — overbank imminent. |

At Watch and above the notification routes through Android's alarm stream, so it sounds at
alarm volume through silent, vibrate and Do Not Disturb. Any official NWS flood product
raises the floor independently — a forecaster knows things two buried probes do not.

## What v1 does not claim

Every threshold above is a placeholder until real storms say otherwise. The numbers that
matter are properties of *this* basin, and no amount of code shortens the wait — they get
measured by watching it rain. So every alert the system sends carries its own disclaimer:
**thresholds are not yet field-calibrated — verify before acting.**

v1 is the line under the build phase, not the calibration phase. The signal path is
complete and tested end to end; what comes next is weather. **This is a personal,
best-effort early-warning aid, never a substitute for official NWS/NOAA flood warnings. Do
not rely on it as your sole source of flood safety information.**

## Repo structure

```
firmware/         Moteino creek node (Arduino) + ESP32 RFM69 gateway (ESPHome)
ha-packages/      Home Assistant package YAML (sensors, templates, automations)
dashboards/       Lovelace dashboards (flood-watch + operator console)
rate_of_rise/     HA add-on: nightly dataset builder + prediction service
docs/             Project docs, including open questions and decisions log
.github/          CI: runs the add-on test suite on every push
repository.yaml   Marks the repo as an installable HA add-on store
creek-flood-warning-spec.md   Source-of-truth project specification
```

## Hardware

- [DFRobot SEN0676](https://www.dfrobot.com/product-2959.html) 80 GHz FMCW radar water-level sensor
- Moteino M0 (LowPowerLab) — creek node MCU with onboard RFM69HW
- RFM69HW 915 MHz modules — point-to-point radio link (node TX + gateway RX)
- ESP32 — gateway at house (RFM69HW RX + WiFi + MQTT)
- CN3791 MPPT solar charger + 18650 Li-ion pack + 6–7 W solar panel
- Ecowitt weather station + WH51 soil moisture probes
- Home Assistant OS on a mini PC

## Status

**Instrument phase: DEPLOYED & OPERATIONAL.** The Moteino M0 + RFM69HW creek node was 
deployed to the pole on 2026-09-19 and is running continuously, reporting every 60 s 
with stable battery voltage and good RSSI (−72 dBm). Hardware defensive handling for 
RFM69HW sleep state (explicit RST pulses + re-initialization on wake) and SPIFlash 
busy-wait fixes ensure unattended operation. 24+ hours of field data show the sensor 
responding appropriately to rainfall-driven water level changes.

Actively being built in phases: ~~instrument~~ → ingest → collect & correlate → predict →
harden. See [creek-flood-warning-spec.md](./creek-flood-warning-spec.md) for the full spec,
[docs/project-knowledge.md](./docs/project-knowledge.md) for an orientation to the
codebase, and [docs/open-questions.md](./docs/open-questions.md) for items still being
resolved.

When a storm is coming, [docs/storm-runbook.md](./docs/storm-runbook.md) is the checklist —
what to check, what to write down while it's happening, and how to annotate the event
afterward.

## Disclaimer

This is a personal, best-effort early-warning aid, not a substitute for official NWS/NOAA
flood warnings. Do not rely on it as your sole source of flood safety information.
