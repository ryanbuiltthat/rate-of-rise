# Data sources and modeling

What goes into the add-on's predictions, and how the predictions turn into alerts. This is
the technical companion to the pitch in the [README](../README.md). Code is in
`rate_of_rise/app/`; [project-knowledge.md](project-knowledge.md) is the guided tour of it.

## The 12 data sources

Each source has its own module in `app/sources/` (the gauge and the on-site probes arrive
from Home Assistant entities instead) and its own poll interval. A source that errors keeps
its last good value, so one flaky API never stalls the loop. The fast loop runs every 5
minutes.

| # | Source | What it gives | Module | Refresh |
|---|---|---|---|---|
| 1 | Creek radar gauge (DFRobot SEN0676, on the pole) | Stage, rate of rise, recent stage history | HA entity from the gateway | 60 s |
| 2 | On-site weather station (Ecowitt) | Rain totals over 1/3/6/24/72 h, rain intensity, temperature | `rain.py` | each loop |
| 3 | Soil moisture ×2 (Ecowitt WH51) | Antecedent wetness, ponding flag | HA entities | each loop |
| 4 | NWS gridded forecast | Forecast rainfall for the next 6 h and 24 h | `nws.py` | 15 min |
| 5 | NWS active alerts | Flood watch, flood warning and flash flood warning flags | `alerts.py` | 5 min |
| 6 | WPC Excessive Rainfall Outlook | Day 1–3 risk, graded against flash-flood guidance | `ero.py` | 30 min |
| 7 | NEXRAD storm-cell tracks | Inbound cell count, ETA, intensity (dBZ) | `radar_cells.py` | 5 min |
| 8 | Upstream rain gauges (Weather Underground) | Rain falling upstream of the site | `wu.py` | 10 min |
| 9 | National Water Model | Short-range streamflow forecast for the creek's reach | `nwm.py` | 20 min |
| 10 | USGS reference gauges ×2 | Stage, flow and 3 h rise on nearby off-basin streams | `usgs.py` | 15 min |
| 11 | SNODAS | Snow water equivalent, for rain-on-snow | `snodas.py` | 6 h |
| 12 | Google Flood Forecasting | Severity and trend at the nearest modeled gauges, plus flash-flood risk polygons at the site | `google_floods.py` | 30 min |

One more input is derived, not fetched: the **antecedent precipitation index** (`apindex.py`),
a running summary of recent on-site rain that stands in for how wet the whole basin is.

Source 12 is two reads of the same API. The gauges it models are neighbouring rivers, never
this creek, so Google's output is capped at Watch and can't raise a Warning on its own.

### How the sources line up in time

The sources cover different lead times, and the models lean on each where it's strongest:

- **Days ahead:** the WPC outlook (6) exists before anything is on radar, and is the only
  input that grades rain against what the ground can absorb.
- **Hours ahead:** NEXRAD cell tracks (7) and the NWS forecast (4). Radar is the only input
  that leads for storms arriving from the west and northwest.
- **Right now:** the on-site gauge (2), upstream gauges (8), soil (3), and the creek itself (1).
- **Context:** NWM (9), USGS (10), SNODAS (11) and Google (12) say how the wider region is
  behaving.

## Three models and a rule set

All three models are small XGBoost models, retrained every night at 03:00 (option
`nightly_retrain_hour`) on the add-on's own record of this creek. XGBoost is used because it
trains and predicts through missing values, and most rows have gaps (a source that hasn't
answered yet is normal).

| Model | Question | Inputs | Drives alerts? |
|---|---|---|---|
| Rise probability (`rise.py`) | Will the creek come up 0.5 in within 1 h, or 1.0 in within 3 h? | 11 | No. Published for people to read. |
| Predicted crest (`crest.py`) | How high will it get in the next 3 h, and how many minutes until it does? | The same 11 | Only to confirm an Emergency (below) |
| Flood probability (`train.py`) | Will the creek reach Warning in the next 3 h? | 49, drawn from all 12 sources | Off by default (`ml_drives_alerts: false`) |

**Rise and crest** learn from every storm, not only floods, so they have something to learn
from now. Their 11 inputs are on-site rain (1 h, 3 h), upstream rain (1 h, 3 h), the 6 h
rain forecast, mean soil moisture, the antecedent precipitation index, the ETA of an inbound
radar cell, and three measures of the creek's own recent movement: rate of rise, change over
the last hour, and height above its 6-hour low. Those last two are what separate "raining, no
response yet" from "already answered". Together these use sources 1, 2, 3, 4, 7 and 8.

Crest is a quantile model, so one fit gives the 10th, 50th and 90th percentile of the rise
(an 80 % range). Every crest payload also carries the highest stage in its training data, and
`beyond_training` flags when the top of the range goes past it. That matters because the
record so far contains rises of an inch or two, and what the creek does over the bank isn't
in it yet.

**Flood probability** is the original "will it flood?" model. It needs real Warning-level
storms to learn from, and there haven't been any, so it mostly waits. Until it can be scored
it runs in shadow: its number is published and recorded every loop, but the alert tiers use a
simple rule-based estimate (rate of rise, plus a bump when the soil is ponding).

### How models are scored

A 5-minute series has hundreds of near-identical rows per storm, so splitting by row would
test a model on the storm it trained on. Rise and crest are scored leave-one-storm-out:
each storm is predicted by a model that never saw it. The reported metrics say how many
independent episodes they rest on, because with a handful of storms the numbers are thin.

A new model never promotes itself. A candidate is shadowed only once it has been scored
against held-out data and caught something there (`registry.is_validated`: it needs a
nonzero hit rate, not only an AUC). Promotion and rollback are explicit commands, and
`min_events_for_ml` (default 10 storms) must be met before a promoted model can drive
alerts.

## From predictions to alerts

`tiers.py` turns everything above into one of five levels. Each rung is the lowest
condition that reaches it, and every rule that fires adds a plain-language reason:

| Tier | Name | Condition |
|---|---|---|
| 0 | All-clear | Nothing elevated. |
| 1 | Advisory | Forecast rain onto already-wet ground, or a WPC excessive-rain risk. |
| 2 | Watch | Rain measured upstream, or radar cells inbound. A radar Watch is held 30 minutes after the cell passes. |
| 3 | Warning | The creek is answering: stage at 24 in (2.0 ft), or rising 0.05 in/min. Critical push from here up. |
| 4 | Emergency | Stage at 30 in (2.5 ft) and climbing. |

- **Tiers 1 and 2 don't need the creek gauge.** They run on forecast and rainfall, so the
  system could warn before the radar was mounted.
- **A signal strong enough fires alone; a marginal one needs primed ground.** For example, a
  Moderate or higher WPC risk raises Advisory by itself, while a Slight risk does so only when
  the soil or the API index is already wet.
- **A flood probability can't raise Emergency alone.** At 80 % it also needs the creek to
  agree: stage of at least 2.0 ft now, or a trusted predicted crest of 2.5 ft or more.
  Otherwise it's held at Warning and the reason says so. This exists because an unproven
  model once read 82–99 % on ordinary one-foot rises.
- **Official NWS products set a floor.** A Flood Watch floors the tier at Advisory, a Flood
  Warning at Watch, and a Flash Flood Warning at Warning. The floor never lowers a tier the
  system already earned.
- **Rate of rise is checked against the radio link.** A rate computed across a node dropout is
  skipped, and after a reconnect it has to hold for two clean samples before it can earn a
  Warning, so a gap in the data can't pose as a flood.

## Measuring the response lag

`lag.py` estimates the warning window, meaning how long after the rain the creek responds. It
reads each storm the creek clearly answered (0.5 in or more) straight off the record: heaviest
hour of rain to crest, and storm start to the creek starting up. It reports the median across
storms. Cross-correlating rain against stage is kept only as a fallback; it found nothing on
this creek because 5-minute stage steps are mostly sensor flicker.

## What isn't calibrated yet

Every threshold on this page is a placeholder until real storms say otherwise. Tier limits
come from the surveyed geometry, not from observed floods, and the models have only seen
small rises. That's why every alert carries the line *thresholds are not yet field-calibrated,
verify before acting*.
