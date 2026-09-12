# Creek Flood Early-Warning System

[![tests](https://github.com/ryanbuiltthat/rate-of-rise/actions/workflows/tests.yml/badge.svg)](https://github.com/ryanbuiltthat/rate-of-rise/actions/workflows/tests.yml)

A DIY flood early-warning system for a small, flashy creek basin in the US Northeast — built on Home Assistant, a Moteino M0 radio-linked stream gauge, and predictive flood-probability modeling.

No official USGS gauge exists on the creek, and the nearest ones are off-basin or downstream. This project instruments the creek directly and fuses that reading with upstream rainfall, soil saturation, forecast precipitation, storm-cell tracking and National Water Model data, to give advance warning before water rises rather than an alarm once it has.

> **Site details are deliberately generalized** throughout this repo — coordinates, creek and place names, and station IDs. Anything site-specific is supplied through the add-on's own configuration, which lives in Home Assistant rather than here.

## How it works

1. **Instrument** — An 80 GHz FMCW radar (DFRobot SEN0676) on a creekside pole measures the water surface to ±5 mm over Modbus RTU into a Moteino M0. The node transmits on RFM69HW (915 MHz) to an ESP32 gateway at the house, which converts raw distance into depth above the creekbed and publishes it to Home Assistant. Solar-powered, ~25 mA average draw.
2. **Ingest** — A local add-on pulls upstream rain-gauge data, NWS/NOAA forecasts, WPC excessive-rainfall outlooks, NEXRAD storm-cell tracks, SNODAS snowpack, National Water Model reach forecasts, USGS reference gauges, and on-site rain, temperature and soil-moisture probes.
3. **Correlate & predict** — The add-on builds a nightly dataset of storm events and fits the rainfall→response relationship for this specific basin, starting with threshold rules and graduating to a trained model that outputs flood probability with lead time.
4. **Alert** — A five-level tier ladder (All-clear / Advisory / Watch / Warning / Emergency) drives phone pushes, escalating to a critical alarm-stream notification that sounds through silent and Do Not Disturb. Any official NWS flood product for the area floors the tier independently of the model.

## Repo structure

```
firmware/         Moteino creek node (Arduino) + ESP32 RFM69 gateway (ESPHome)
ha-packages/      Home Assistant package YAML (sensors, templates, automations)
dashboards/       Lovelace dashboards (flood-watch + operator console)
creek_modeling/   HA add-on: nightly dataset builder + prediction service
docs/             Project docs, including open questions and decisions log
.github/          CI: runs the add-on test suite on every push
repository.yaml   Marks the repo as an installable HA add-on store
creek-flood-warning-spec.md   Source-of-truth project specification
```

## Hardware

- [DFRobot SEN0676](https://www.dfrobot.com/product-2959.html) 80 GHz FMCW radar water-level sensor
- Moteino M0 (LowPowerLab) — creek node MCU with onboard RFM69HW
- RFM69HW 868 MHz modules — point-to-point radio link (node TX + gateway RX)
- ESP32 — gateway at house (RFM69HW RX + WiFi + MQTT)
- CN3791 MPPT solar charger + 18650 Li-ion pack + 6–7 W solar panel
- Ecowitt weather station + WH51 soil moisture probes
- Home Assistant OS on a mini PC

## Status

Actively being built in phases: instrument → ingest → collect & correlate → predict → harden. See [creek-flood-warning-spec.md](./creek-flood-warning-spec.md) for the full spec and [docs/open-questions.md](./docs/open-questions.md) for items still being resolved.

When a storm is coming, [docs/storm-runbook.md](./docs/storm-runbook.md) is the checklist — what to check, what to write down while it's happening, and how to annotate the event afterward.

## Disclaimer

This is a personal, best-effort early-warning aid, not a substitute for official NWS/NOAA flood warnings. Do not rely on it as your sole source of flood safety information.
