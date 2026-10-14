# Rate of Rise

[![tests](https://github.com/ryanbuiltthat/rate-of-rise/actions/workflows/tests.yml/badge.svg)](https://github.com/ryanbuiltthat/rate-of-rise/actions/workflows/tests.yml)

**Warning arrives before the water does.**

There's a small creek near my house that floods fast and has no official gauge. So I built
one, and taught it to warn me before the water gets here.

A radar on a pole measures the creek's height every minute and sends it to the house by
radio. The add-on checks that against the forecast, upstream rain gauges, storm tracks,
soil wetness and the National Weather Service, then answers one question: **is the creek
about to come up, and how high?** If the answer is yes, my phone goes off, even on silent.

It runs on Home Assistant. The brains ship as the **Rate of Rise** add-on, which is where
the project gets its name.

> **This is a personal, best-effort warning aid. It is not a substitute for official
> NWS/NOAA flood warnings.** Don't make it your only source of flood safety information.

## Why build it

The creek drains about 18 square miles and rain-to-crest is measured in tens of minutes.
The nearest USGS gauges are off the creek, so they can't say how high the water is right now.

A gauge that tells you it's flooding is just a logger. This one is meant to say "it's about
to," early enough that you can still move things instead of rescuing them.

## How it works

1. **Measure.** An 80 GHz radar looks down at the water from a pole, accurate to ±5 mm. A
   small battery-and-solar node reads it once a minute and sends it over a private 915 MHz
   radio link to a gateway at the house. No WiFi at the creek, no cellular, no subscription.
2. **Gather.** The add-on pulls in 12 live data sources, listed below, every few minutes.
3. **Predict.** It learns from every storm this creek has and answers three questions: will
   the creek come up in the next one to three hours, how high will it get and how soon, and
   will it reach Warning level.
4. **Alert.** Five levels: All-clear, Advisory, Watch, Warning, Emergency. From Warning up
   (the creek at 24 in, or rising fast) the phone alert comes through silent and Do Not
   Disturb. An official NWS flood warning raises the level whatever the model says.

## The 12 data sources

1. **Creek radar gauge:** the creek's height and how fast it's rising
2. **On-site weather station (Ecowitt):** rain and temperature at the house
3. **Soil moisture probes (2× Ecowitt WH51):** how wet the ground already is
4. **NWS forecast:** rain expected over the next 6 and 24 hours
5. **NWS alerts:** official flood and flash flood watches and warnings
6. **WPC Excessive Rainfall Outlook:** rain graded against what the ground can absorb
7. **NEXRAD storm tracks:** storm cells heading our way, and when they arrive
8. **Upstream rain gauges (Weather Underground):** rain already falling upstream
9. **National Water Model:** NOAA's streamflow forecast for the creek's reach
10. **USGS stream gauges (2):** how nearby streams are responding
11. **SNODAS snowpack:** snow that rain could melt
12. **Google Flood Forecasting:** Google's flood and flash-flood forecasts for the area

## How it predicts

Three small machine-learning models retrain every night on this creek's own storms:

- **Rise:** the chance of a half-inch rise within an hour, or an inch within three.
- **Crest:** how high the creek will get in the next three hours, with a range, and how many
  minutes until it gets there.
- **Flood:** the chance of reaching Warning level. It needs real floods to learn from, so
  for now it runs in the background and doesn't drive alerts.

Rise and crest look at the rain falling here and upstream, the forecast, soil wetness, any
storm cell inbound and the creek's own recent movement. The flood model looks at all 12
sources. Rise and crest are scored on storms they never saw in training, and none of the
three is allowed to drive an alert until it has proven itself.

The alert levels themselves come from plain rules, so you can always see why one fired. The
models can only back the rules up: a high flood probability alone never raises an Emergency
unless the creek's actual height or the predicted crest agrees. Details are in
[docs/modeling.md](./docs/modeling.md).

## What's on the pole and in the house

**At the creek**
- DFRobot SEN0676 80 GHz radar water-level sensor
- LowPowerLab Moteino M0 with an RFM69HW 915 MHz radio
- Adafruit bq24074 solar charger, 6 V / 7 W panel, and a 6 Ah pack of four 18650 cells
- Pololu U1V11F5 boost converter, which switches the radar on only when it takes a reading
- A bimetal switch that stops charging below 5 °C, since lithium cells can't charge in the cold

The whole node averages about 1.7 mA, and a full pack runs it for months with no sun.

**At the house**
- Gateway: a Seeed XIAO ESP32-C3 with an RFM69HW radio, running ESPHome
- Gateway v2, in trial and built to replace it: an Adafruit ESP32-S3 Feather with an
  RFM69HCW radio wing and an Adalogger wing (microSD card and clock). It records everything
  it hears and fills any gap in Home Assistant's history after an outage. It also drives a
  2.4" touch display that shows stage, rain and link status without needing Home Assistant.
- Ecowitt weather station and two WH51 soil moisture probes
- Home Assistant OS on a mini PC

## Where it stands

The creek node went on the pole on 2026-09-19 and has been reporting every minute since.
Radio, ingest, prediction and alerting all work end to end, with 650+ automated tests that
run on every push.

**The thresholds are placeholders until real storms prove them.** They can only be measured
by watching it rain. Every alert says so: *thresholds are not yet field-calibrated, verify
before acting.*

Site details (coordinates, creek and place names, station IDs) are deliberately left out of
this repo. They live in the add-on's configuration in Home Assistant.

## Where to look next

| | |
|---|---|
| [creek-flood-warning-spec.md](./creek-flood-warning-spec.md) | The full project spec |
| [docs/project-knowledge.md](./docs/project-knowledge.md) | A guided tour of the code |
| [docs/open-questions.md](./docs/open-questions.md) | What's still being worked out |
| [docs/modeling.md](./docs/modeling.md) | The data sources, models and alert rules in detail |
| [docs/node-hardware.md](./docs/node-hardware.md) | Radar, power and solar details |
| [docs/storm-runbook.md](./docs/storm-runbook.md) | What to do when a storm is coming |
| [firmware/README.md](./firmware/README.md) | Wiring, building and flashing the node and gateways |
| [rate_of_rise/](./rate_of_rise/) | The Home Assistant add-on |

## Disclaimer

This is a personal, best-effort early-warning aid, not a substitute for official NWS/NOAA
flood warnings. Do not rely on it as your sole source of flood safety information.
