# Creek Flood Early-Warning System — Project Specification

**Location:** Lackawanna County, northeastern Pennsylvania (`<site lat>`,`<site lon>`), mid-watershed on the creek
**Motivation:** Prior 100-year storm event caused 40" of basement flooding. Goal is a tiered early-warning system that predicts flood *probability* before water rises — not just threshold alarms.

---

## 1. Watershed Context

- Creek: ~8.7 mi long, ~18 mi² basin in Lackawanna County, northeastern Pennsylvania. Flows
  NW from a headwaters swamp, through several small upstream communities, to its confluence
  with a larger creek.
- Sensor site is mid-watershed; upstream drainage (~half the basin) lies SE toward the
  upstream half of the watershed.
- **No official gauge exists on the creek.** USGS `<usgs wq 1>` (an upstream village) and `<usgs wq 2>` (an upstream village) are water-quality sites only. ~~USGS `<usgs bad id>` (a nearby reach)~~ **does not exist** — NWIS returns "no sites found" for that number; it was a bad ID, not a retired gauge. The nearest gauges that actually publish continuous instantaneous values are:
  - **USGS `<usgs downstream>`** — a receiving river downstream (~7 mi SE). An adjacent basin, but it drains the same upstream upland that forms the creek's upstream half, so it sees substantially the same rain. Best available response analog.
  - **USGS `<usgs adjacent>`** — a receiving creek in an adjacent basin (~11 mi W), downstream of the confluence; much larger drainage, longer lag.
  - SRBC CIM near the confluence — downstream.

  All are off-basin or downstream: useful for validation and for empirically estimating the rainfall→response lag, never as a stand-in for creek stage.
- Flashy small basin: expected rainfall-to-crest lag is likely tens of minutes to a few hours. This lag is the warning window; empirically measuring it is a core project outcome.
- Regional hazard note: rain-on-snow events are a major flood driver in this region; snowpack state must be a model input (via SNODAS data, no hardware).

## 2. Hardware (on hand unless noted)

| Component | Role | Status |
|---|---|---|
| DFRobot SEN0676 (80 GHz FMCW radar, ±5 mm, ±3° lens, 0.15–40 m, UART Modbus RTU, 3.5–5 V, ~30 mA) | Primary creek level | **Installed** |
| Moteino M0 (LowPowerLab, SAMD21 + RFM69HW socket) | Creek node MCU, Arduino sketch, RFM69HW TX (915 MHz) | **Installed** |
| RFM69HW 915 MHz modules × 2 | Node TX + gateway RX, point-to-point radio link | **Installed** |
| ESP32 (any variant) | Gateway: RFM69HW RX + WiFi + MQTT bridge | On hand |
| ~~ESP32-C6~~ | ~~Creek node MCU, ESPHome, WiFi~~ — **superseded by Moteino M0 + RFM69HW** (lower power, better range through trees) | ~~On hand~~ |
| bq24074 linear solar charger + panel + 1S4P Li-ion pack (18650, 6 Ah) | Creek node power. The node draws ~1.7 mA (open question #17), so the linear charger's ~33 % loss going 6 V → 4 V does not matter and the CN3791 MPPT upgrade is not needed (open question #12). | **Installed.** Pack sizing: open questions #11–12. |
| Ecowitt weather station (uploads to Weather Underground) | On-site rain, temp, wind | Installed |
| Ecowitt WH51 soil moisture ×2 | Antecedent wetness | **Installed (×2)** |
| Aluminum pole at the creek's edge (property low point), guy-wired above | Sensor mount | **Built** (see mounting geometry below) |
| Submersible pressure transducer (4–20 mA, stilling pipe) | Dissimilar-redundancy backup level sensor | Future phase |
| Creek camera (solar WiFi or PoE) | Visual confirmation, storm archive | Future phase |

### Mounting geometry (as built — datum is the creekbed = 0)

> **Superseded.** This section previously specified a mount 9–10 ft above low water (~6–7 ft
> above bank) at a different candidate site with a higher bank. That site was abandoned: it
> was problematic for solar. The pole stands at the property low spot instead, and the numbers
> below are the surveyed as-built. Anything elsewhere in this document that reasons from
> "9–10 ft above low water" or a "+7 ft design flood" is reasoning about the old site.

| | Height above creekbed |
|---|---|
| Sensor face (surveyed 2026-09-12) | **43.5 in** (1105 mm) |
| Sensor range ceiling (blanking zone, 0.15 m) | 37.6 in |
| Bank top (measured 2026-09-14) | **44.25 in** |
| Emergency threshold | 30 in |
| Warning threshold | 24 in |

- Datum is the **creekbed**, not a low-water surface: it is what can be surveyed precisely, and
  depth above the bed reads as true water depth rather than going negative on a dry bed.
- **Bank top was re-measured 2026-09-14** at 44.25 in, ~6.75 in higher than the earlier eyeball
  estimate (~37.5 in) that #14's "clamp coincides with overtopping" reasoning was built on. The
  range ceiling and bank top do **not** coincide: the sensor now stops measuring ~6.65 in of
  depth *before* the creek reaches bank top, not almost exactly at that point. Readings inside
  the blanking zone are still **clamped to the ceiling, never published as unavailable** — see
  open question #14 — but that clamp currently kicks in early rather than right at overtopping.
- Both alert thresholds sit below the ceiling, so the ladder escalates fully on the way up.
- Plumb the sensor (±3° beam; verify with bubble level). Rigidity matters: pole sway = level noise.
- **The system's target is the creek cresting its bank**, not a 100-year flood. Overbank depth
  beyond ~37.6 in is not measurable at this height; raising the pole (#16, a short extension
  planned this week) would also close the blanking-zone gap above, buy 2–3 ft of measurable
  overbank, and materially better odds of the sensor surviving debris. Installation height is
  unchanged for now — re-measure and update once the extension is in.

### Creek node firmware (Arduino, Moteino M0)
- SEN0676 on hardware UART1 (RX/TX), Modbus RTU polling at 115200 baud (datasheet default).
- RFM69HW on SPI (Moteino onboard socket) + DIO0 interrupt. **915 MHz** (North American ISM band), encrypted, node ID 1. 868 MHz is the European band — a mismatch between node and gateway is silent: both radios initialize fine and simply never hear each other.
- Report level every 60 s; pack JSON payload: `{distance_mm, battery_mv}`.
- Transmit to gateway (node ID 2) via RFM69, then deep sleep (~6 µA on SAMD21).
- Battery voltage via ADC + voltage divider. Sensor powered from switched 5 V rail (boost/buck EN pin).
- Average draw ~1.7 mA, measured 2026-09-25 (open question #17); the solar panel is ample.
- Firmware source: `firmware/moteino_creek_node/`. See `firmware/README.md` for wiring and build instructions.

### Gateway firmware (ESPHome, Seeed XIAO ESP32-C3)
- RFM69HW on SPI, receives packets from the creek node.
- Decodes the payload on-device and exposes it as native ESPHome entities over the API:
  water level, node battery, per-packet RSSI, and a connectivity binary sensor.
- Always-on, plugged in at house — no power constraints.
- Firmware source: `firmware/esp32_rfm69_gateway/`. The earlier byte-for-byte MQTT
  republish (`creek/node_1/*`) was dropped — nothing consumed those topics.

## 3. Data Sources & APIs

| Source | Registration | Use |
|---|---|---|
| Creek node (Moteino M0 → RFM69 → ESP32 gateway → ESPHome API) | HA entities `sensor.creek_gateway_stage` (ft), `sensor.creek_gateway_creek_depth` (in), `sensor.creek_gateway_creek_node_battery` | Real-time stage; the ground truth |
| Ecowitt local integration in HA | — | Real-time on-site rain, soil moisture (WH51) |
| Weather.com / WU PWS API | Key already held (via Ecowitt→WU upload; key in WU member settings) | Upstream neighbor PWS rainfall: the upstream corridor. Stations in use: `<upstream PWS 1>`, `<upstream PWS 2>` (open question #4 — 2 of the 3–5 wanted). |
| NWS `api.weather.gov` | None (User-Agent header) | Gridded QPF (forecast precip) for `<site lat>`,`<site lon>`; active Flood Watch/Warning products for the county |
| NOAA NWPS API `api.water.noaa.gov/nwps/v1` | None | National Water Model reach forecast for the creek segment — reach `<nwm reach id>` (open question #3, resolved) |
| USGS Water Services | None | Instantaneous values — gauges `<usgs downstream>` (downstream reach) and `<usgs adjacent>` (adjacent-basin reach); see §1 |
| Google Flood Forecasting API `floodforecasting.googleapis.com` | Google Cloud project + enable API + API key | **Built (Addendum C 2i).** `gauges:searchGaugesByArea` over a 25 mi box → the gauges Google models near the site, real and virtual (hybas), incl. non-quality-verified; then `floodStatus:queryLatestFloodStatusByGaugeIds` for forecast severity and trend. Gauge-model thresholds are not ingested; `v1.flashFloods` is ingested separately — see 2j |
| SNODAS (NOHRSC) | None | Snow water equivalent for grid cell — rain-on-snow feature |

## 4. Architecture

```
[Creek node: Moteino M0 + SEN0676 + solar] --RFM69HW 915 MHz-->
  [Gateway: ESP32 + RFM69HW] --WiFi/MQTT--> [Home Assistant (mini PC)]
[Ecowitt GW: rain, WH51 soil] --local--> [HA]
[WU PWS / NWS / NWPS / USGS / Google Floods / SNODAS] --REST sensors--> [HA]
[HA recorder / InfluxDB] <----> [Modeling service (HAOS add-on)]
[Modeling service] --MQTT--> [HA: flood_probability, predicted_crest, lag_estimate, model_health]
[HA automations] --> alert tiers --> mobile notifications / TTS / etc.
```

**Layer 1 — HA package** (`ha-packages/creek_warning.yaml`): MQTT sensors from the creek gateway, REST sensors per API, template sensors (rate-of-rise in/min, 1/6/24/72-h rain accumulations, Antecedent Precipitation Index), alert automations, sensor-fault watchdogs (stale data, radar/pressure divergence when transducer added).

**Layer 2 — Modeling service** (Python, containerized as a **local Home Assistant add-on** — HA install is HAOS; see [Addendum A](#addendum-a--modeling-service-as-a-haos-add-on-resolves-open-question-1)):
- **Fast loop (5 min):** compute flood probability + predicted stage from live features; publish via MQTT.
- **Nightly batch:** append day's data to dataset (Parquet/SQLite), recalibrate/retrain, version model artifact, log skill metrics (hit rate, false alarms, lead time), publish model_health.
- Post-storm: manual review + model promotion step.
- Compute: mini PC CPU is sufficient. **Explicitly out of scope: Pi5/Hailo-8 or any NPU** (tabular model, trains in seconds). Revisit only if a creek camera + Frigate vision analytics is added later.

**Layer 3 — HACS custom integration** (end state, after model proves out): config flow UI for API keys, station IDs, thresholds, alert tier tuning. Do not build before Phase 4.

## 5. Model Approach

- Target: creek stage (and/or exceedance probability of tier thresholds) at +30 min, +1 h, +3 h horizons.
- Features: current stage, rate-of-rise, on-site + upstream rain accumulations (1/3/6/24/72 h), API/soil moisture (WH51), QPF next 6/24 h, NWM reach forecast, Google flood status (severity, trend and distance to the gauge that set them), season, SNODAS SWE, temperature (rain-on-snow flag).
- Start simple → escalate only as data justifies: (1) empirical lag + linear rainfall-runoff response conditioned on soil moisture; (2) gradient boosting (XGBoost/LightGBM) once ≥ ~10 significant rain events are captured; (3) revisit later.
- Honest constraint: no meaningful model tuning until several storms are recorded. Early months = data collection + threshold-based alerting only.

## 6. Alert Tiers

Implemented in `rate_of_rise/app/tiers.py`, which emits the four escalation tiers below
*plus* the explicit all-clear this section calls for — so the published scale is 0–4, with
the tier number one higher than this table's original 0–3 numbering:

| Level | Tier | Trigger basis | Example condition (tune with data) | Needs the creek gauge? |
|---|---|---|---|---|
| 0 | All-clear | Nothing elevated | — | — |
| 1 | Advisory | Forecast risk | QPF ≥ X" in 24 h AND soil moisture ≥ Y% | No |
| 2 | Watch | Upstream rain materializing | Upstream PWS accumulation ≥ X" in Y h, creek not yet responding | No |
| 3 | Warning | Creek responding | Stage ≥ A ft OR rate-of-rise ≥ B in/min sustained C min | Yes |
| 4 | Emergency | Flood in progress / imminent | Stage ≥ bank − margin OR model P(overbank) ≥ threshold | Yes |

Advisory and Watch are deliberately gauge-independent: they run off forecast and rainfall
features that flow without the creek node, so the system warns during the wait for the
SEN0676. Warning and Emergency stay dormant until the node reports stage. Each tier also
publishes the reasons that fired it.

Original table, retained for reference:

| Tier | Trigger basis | Example condition (tune with data) |
|---|---|---|
| 0 Advisory | Forecast risk | QPF ≥ X" in 24 h AND soil moisture ≥ Y% |
| 1 Watch | Upstream rain materializing | Upstream PWS accumulation ≥ X" in Y h, creek not yet responding |
| 2 Warning | Creek responding | Stage ≥ A ft OR rate-of-rise ≥ B in/min sustained C min |
| 3 Emergency | Flood in progress / imminent | Stage ≥ bank − margin OR model P(overbank) ≥ threshold |

Each tier maps to escalating HA actions (notification → persistent alarm → wake-the-house). Include an "all-clear" state and hysteresis to prevent flapping. NWS Flood Warning for the county force-promotes to ≥ Tier 1.

## 7. Phases & Deliverables

**Phase 1 — Instrument (weekend 1–2):**
~~ESPHome YAML for creek node~~ **superseded** by Moteino M0 + RFM69HW architecture
(active firmware is in `firmware/`; the retired ESPHome node config was removed in v1).
Creek node firmware (Arduino sketch, `firmware/moteino_creek_node/`) reads SEN0676 via
Modbus RTU, transmits JSON over RFM69HW to the ESP32 gateway
(`firmware/esp32_rfm69_gateway/`), which bridges to MQTT. **Written against datasheets
and never run on hardware** — `firmware/README.md` carries the bench-test procedure.
Pole/arm install per §2 geometry; ~~WH51 probes into Ecowitt~~ **done (×2 installed)**; verify long-term statistics recording in HA.

- **Follow-up:** Confirm WH51 entities appear in HA via the Ecowitt integration and are captured in recorder long-term statistics (check `state_class`); these feed the nightly dataset builder.

**Phase 2 — Ingest (weeks 1–4, parallel):**
`ha-packages/creek_warning.yaml` with all REST sensors; enumerate upstream WU station IDs; resolve NWM reach ID; ~~register Google Floods API and run `searchGaugesByArea` over the watershed~~ **done** (Addendum C 2i — the search runs on a 25 mi box around the site rather than a hand-drawn watershed polygon, and re-runs daily); SNODAS fetch; data-quality watchdogs.

**Phase 3 — Collect & correlate (months 1–3):**
~~Nightly dataset builder~~ **done** (`app/dataset.py`: per-day JSONL parts, consolidated
nightly into Parquet); ~~storm event log (annotated)~~ **done** (`app/storms.py`, events
defined by rainfall so the record stays valid once the gauge lands); ~~first lag/response
estimates~~ **done** (`app/lag.py`, cross-correlation); ~~threshold-based alerts live
(conservative values)~~ **done** (`app/tiers.py`, Phase 2 — and the forecast-driven tiers
run without the gauge); ~~downstream-gauge sanity comparisons~~ **done** (lag falls back to
USGS and labels itself as a proxy).

What Phase 3 still needs is **time, not code**: storms have to actually happen. The lag
estimate stays a USGS-proxy number until the SEN0676 is mounted, and the thresholds
throughout stay uncalibrated until the storm log has entries to fit against.

**Phase 4 — Predict (after ~10 events):**
~~Fast-loop inference service~~ **done** (`app/model.py`, gated by `min_events_for_ml`);
~~model registry~~ **done** (`app/registry.py`); ~~nightly retrain~~ **done**
(`app/train.py`, gradient boosting — see Addendum D); tier logic upgraded from thresholds
to probability **partially done** — `tiers.py`'s Watch/Warning rules already read
`flood_probability` when it is available, they just have not had a real one to read yet;
skill dashboard (lead time achieved, false alarm rate) **done**, published in
`registry.snapshot()`'s `active_metrics`/`candidate_metrics` and shown on the Operator tab.

The pipeline is code-complete and unit-tested against synthetic storms (Addendum D), and
has never seen real data: `min_events_for_ml` gates it on ~10 captured storms, and even
past that gate it needs `stage_ft` or `rate_of_rise_in_min` to actually cross Warning at
least twice to have anything to learn — both are None until the SEN0676 exists. What
Phase 4 needs, like Phase 3, is time, not code.

**Phase 5 — Harden & polish:**
Pressure-transducer redundancy + divergence alarm; creek camera; HACS integration with config flow; documentation/runbook.

## 8. Conventions & Environment

- Dev environment: Windows, Git Bash, VS Code + Claude Code; corporate SSL notes apply on work machine only — prefer home environment for this repo.
- Creek node firmware is Arduino (Moteino M0 + RFM69HW); gateway is Arduino (ESP32 + RFM69HW + MQTT). Sketches in `firmware/`. ESPHome remains the standard for other devices in the fleet (16-ch CT power meter, etc.), and the gateway itself is an ESPHome device; the retired ESP32-C6 creek node config was removed in v1.
- HA config as packages under version control; secrets via `secrets.yaml` / HA credentials — never committed.
- Modeling service: Python 3.11+, packaged as a **local HAOS add-on** (`config.yaml` + `Dockerfile` + `run.sh`), data in Parquet + SQLite under `/data`, MQTT for HA interface. See [Addendum A](#addendum-a--modeling-service-as-a-haos-add-on-resolves-open-question-1).
- Every automation that can wake the family must be testable via a dry-run script/service.
- Frontend: any custom Lovelace cards / flood dashboard follow the HA design system — design tokens (no hardcoded colors), light/dark parity, WCAG AA contrast, `<ha-card>` + MDI icons, responsive breakpoints. Ref: <https://design.home-assistant.io/>. (No frontend exists yet; applies when the dashboard/cards land — see Phase 5. Reuse the Prism theme where possible.)

## 9. Open Questions (resolve in Phase 1–2)

1. ~~HA install type on mini PC (HAOS/Supervised → add-on path; Container → sidecar docker-compose path).~~ **RESOLVED: HA install is HAOS.** Layer 2 modeling service is built as a local add-on — see [Addendum A](#addendum-a--modeling-service-as-a-haos-add-on-resolves-open-question-1).
2. Google Floods API: does a virtual gauge (hybas) land on the creek or nearest the receiving river reach? What are its thresholds?
3. ~~NWM reach ID for the creek's segment at `<site lat>`,`<site lon>`.~~ **RESOLVED: `<nwm reach id>`** (reach position `<reach lat>`,`<reach lon>`, ~100 m from the site; verified live against the NWPS API).
4. ~~Which 3–5 upstream PWS stations are reliable (uptime, tipping-bucket quality)?~~ **PARTLY RESOLVED: `<upstream PWS 1>`, `<upstream PWS 2>`.** Two of the 3–5 wanted; add more from the the upstream corridor as reliable ones are identified.
5. Exact low-water reference datum and surveyed bank height at the sensor site (measure at install).
6. WiFi RSSI at the pole via the outdoor AP (bag test before final mount).
7. WH51 readings are relative (0–100%) and site-specific. After the next soaking rain and a dry stretch, record the empirical "saturated" and "dry" values at each burial spot; these calibrate the Tier 0 soil-moisture threshold.

---

## Addendum A — Modeling service as a HAOS add-on (resolves Open Question #1)

**Decision:** The Home Assistant install on the mini PC is **HAOS** (Home Assistant Operating System, Supervisor-managed). The Layer 2 modeling service is therefore built as a **local add-on**, not a sidecar `docker-compose` service. This addendum supersedes the "Docker container *or* HAOS add-on" hedge in §4 and the `docker-compose.yml` note in §8.

### A.1 Why add-on over sidecar Container

- HAOS does not expose the host Docker socket for user compose stacks; add-ons are the supported way to run custom containers on HAOS.
- The Supervisor provides, for free, exactly the plumbing this service needs: an authenticated **proxy to the HA Core API** (no long-lived token to mint or rotate), **MQTT service discovery** (broker host/credentials injected at runtime), managed lifecycle (auto-start, restart, logs, watchdog), and a **persistent `/data` volume** that survives add-on updates.
- Config UI comes for free: the `schema` block renders a form in **Settings → Add-ons**, so API keys and thresholds are edited in the HA UI instead of a `.env` file. This is a natural stepping-stone toward the Layer 3 HACS integration's config flow (§4).

### A.2 Add-on layout

The add-on **source** lives in the repo at `rate_of_rise/` (per the README repo structure).
Preferred install is via the **Git-based add-on repository** (add the repo URL in
**Settings → Add-ons → Add-on Store → ⋮ → Repositories**), which enables GUI install and
versioned updates. It can also run as a **local add-on**: copy that folder into the HAOS
`/addons/` directory as `/addons/rate_of_rise/` (via the Samba or SSH add-on, or
`addon_config`), where it appears under **Settings → Add-ons → Local add-ons**. (Repo dir = 
`rate_of_rise/`; local install target = `/addons/rate_of_rise/` — same files.)

```text
rate_of_rise/            # repo source (installs to HAOS /addons/rate_of_rise/)
├── config.yaml          # add-on manifest + options schema
├── build.yaml           # per-arch BUILD_FROM (Debian base)
├── Dockerfile           # build recipe (HA base image + Python deps)
├── run.sh               # entrypoint (bashio: read options, export env, exec service)
├── requirements.txt     # pandas, pyarrow, xgboost/lightgbm, paho-mqtt, requests, ...
├── icon.png / logo.png  # optional, for the add-on store card
└── app/                 # the modeling service itself
    ├── __main__.py      # fast loop (5 min) + nightly batch scheduler
    ├── config.py        # options.json + env loader
    ├── features.py      # feature builders (rate-of-rise, APIndex, accumulations)
    ├── model.py         # train / infer / registry
    ├── dataset.py       # Parquet feature rows + SQLite storm-event log
    ├── ha.py            # HA Core API client (Supervisor proxy)
    └── mqtt_client.py   # MQTT publisher for model outputs
```

### A.3 `config.yaml` manifest (with options schema)

```yaml
name: Rate of Rise
version: "0.1.0"
slug: rate_of_rise
description: Flood-probability + predicted-stage inference and nightly retrain for the creek.
url: https://github.com/ryanbuiltthat/rate-of-rise
arch:
  - amd64          # mini PC; add aarch64 only if the host changes
startup: application # start after HA Core is up (needs the API + MQTT)
boot: auto
init: false          # s6-overlay from the base image is the init; run.sh is the service

# --- Supervisor-granted capabilities ---
homeassistant_api: true   # proxy to Core REST API at http://supervisor/core/api
hassio_api: true          # (optional) Supervisor API, e.g. to read add-on/service info
auth_api: false
services:
  - mqtt:need             # auto-discover the Mosquitto broker; creds injected at runtime
map:
  - addon_config:rw       # optional: human-editable configs/notes outside /data
  - share:rw              # optional: drop Parquet exports where other tools can read them

# --- User-configurable options (rendered as a form in the HA UI) ---
options:
  log_level: info
  fast_loop_minutes: 5
  nightly_retrain_hour: 3
  mqtt_base_topic: creek
  publish_prefix: creek          # -> sensor.creek_flood_probability, etc.
  min_events_for_ml: 10          # gate: threshold model until >= N storms captured
  google_floods_api_key: ""
  wu_api_key: ""
  nwm_reach_id: ""
  upstream_pws_ids: []

schema:
  log_level: list(trace|debug|info|notice|warning|error|fatal)
  fast_loop_minutes: int(1,60)
  nightly_retrain_hour: int(0,23)
  mqtt_base_topic: str
  publish_prefix: str
  min_events_for_ml: int(1,100)
  google_floods_api_key: password?
  wu_api_key: password?
  nwm_reach_id: str?
  upstream_pws_ids:
    - str
```

Notes:

- `password?` masks secrets in the UI and marks them optional; keys live in Supervisor-managed options, not in a committed file (consistent with §8's "never commit secrets").
- `services: [mqtt:need]` makes the add-on refuse to start unless an MQTT broker (Mosquitto add-on) is present, and injects host/port/user/pass via `bashio::services mqtt`.
- No `ports:` are published — the service is headless and talks out via MQTT + the Supervisor API proxy. Add a `ports`/`ingress` block later only if a debug/status web UI is wanted.

### A.4 `Dockerfile`

Use the Debian add-on base image (Alpine/musl makes `xgboost`/`lightgbm`/`pandas` wheels painful); it ships `bashio`, `s6-overlay`, and `tempio`.

```dockerfile
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-venv libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --break-system-packages -r /tmp/requirements.txt

COPY run.sh /run.sh
COPY app/ /app/
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
```

(`libgomp1` is the OpenMP runtime XGBoost/LightGBM link against.) `BUILD_FROM` is overridden per-arch by Supervisor at build time via a `build.yaml`, but pinning amd64 as the default is fine for a single mini PC.

### A.5 `run.sh` (entrypoint)

```bash
#!/usr/bin/with-contenv bashio
set -euo pipefail

# --- Options → environment ---
export LOG_LEVEL="$(bashio::config 'log_level')"
export FAST_LOOP_MINUTES="$(bashio::config 'fast_loop_minutes')"
export NIGHTLY_RETRAIN_HOUR="$(bashio::config 'nightly_retrain_hour')"
export MQTT_BASE_TOPIC="$(bashio::config 'mqtt_base_topic')"
export PUBLISH_PREFIX="$(bashio::config 'publish_prefix')"
export MIN_EVENTS_FOR_ML="$(bashio::config 'min_events_for_ml')"
export GOOGLE_FLOODS_API_KEY="$(bashio::config 'google_floods_api_key')"
export WU_API_KEY="$(bashio::config 'wu_api_key')"
export NWM_REACH_ID="$(bashio::config 'nwm_reach_id')"

# --- HA Core API via the Supervisor proxy (no long-lived token needed) ---
export HA_API_URL="http://supervisor/core/api"
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"   # injected by Supervisor

# --- MQTT from the Mosquitto add-on via service discovery ---
if bashio::services.available "mqtt"; then
  export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
  export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
  export MQTT_USER="$(bashio::services 'mqtt' 'username')"
  export MQTT_PASS="$(bashio::services 'mqtt' 'password')"
else
  bashio::exit.nok "No MQTT service available — install/configure the Mosquitto add-on."
fi

# --- Persistent storage (survives add-on updates/restarts) ---
export DATA_DIR="/data"          # dataset.parquet, model registry, accumulator state
mkdir -p "${DATA_DIR}/models" "${DATA_DIR}/datasets"
export SHARE_DIR="/share"        # events.sqlite — hand-annotated, so not add-on-private
mkdir -p "${SHARE_DIR}/rate_of_rise"

bashio::log.info "Starting Creek modeling service (fast loop ${FAST_LOOP_MINUTES}m)…"
exec python3 -m app
```

### A.6 HA access via the Supervisor proxy

Because `homeassistant_api: true` is set, the container reaches HA Core at `http://supervisor/core/api`, authenticating with the `SUPERVISOR_TOKEN` env var that Supervisor injects — **no user-created long-lived access token is required or stored.**

```python
# app/ha.py  (sketch)
import os, requests
_HDRS = {"Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}",
         "Content-Type": "application/json"}
def get_state(entity_id: str):
    r = requests.get(f"{os.environ['HA_API_URL']}/states/{entity_id}",
                     headers=_HDRS, timeout=10)
    r.raise_for_status()
    return r.json()
```

Live inputs (current stage, on-site/upstream rain, soil moisture, QPF/NWM/SNODAS REST sensors from Layer 1) are read either from Core states via this proxy **or**, for history/backfill, from the recorder/InfluxDB as before. Outputs (`flood_probability`, `predicted_crest`, `lag_estimate`, `model_health`) are **published over MQTT** using the discovered broker — matching the §4 architecture diagram — so HA sees them as MQTT sensors and the alert automations (§6) fire off those entities.

### A.7 Persistent storage layout (`/data`)

Supervisor bind-mounts a per-add-on volume at `/data` that persists across restarts and add-on updates; `/data/options.json` holds the current options (already parsed by `bashio::config`). The service owns the rest:

```text
/data/
├── options.json                 # (managed by Supervisor)
├── datasets/
│   └── dataset.parquet          # nightly-appended feature/label rows (§4 batch)
├── models/
│   ├── registry.json                # versioned artifacts + skill metrics (hit rate, FA, lead time)
│   ├── model-<version>.json         # promoted artifact (xgboost native format — Addendum D)
│   └── model-<version>.meta.json    # feature column order + horizon, alongside it
└── state/
    └── last_run.json            # fast-loop / nightly-batch bookkeeping
```

The storm event log is the one exception, and lives in the shared volume instead:

```text
/share/rate_of_rise/events.sqlite   # annotated storm event log (§7 Phase 3)
```

`/data` is private to each add-on, which makes it the wrong home for the only file the
project expects a *human* to edit: the documented `sqlite3 /data/events.sqlite` typed in
the SSH/Terminal add-on opens that add-on's own empty `/data`, succeeding while annotating
nothing. `/share` resolves identically from every add-on and is exported over Samba, so one
path works from a terminal or a GUI SQLite browser. A log left in `/data` by an earlier
version is migrated on first start, and `/data` remains the fallback if `/share` is
unavailable.

This satisfies §4's "append day's data to dataset (Parquet/SQLite), version model artifact" and §7's storm event log without any external volume.

### A.8 Impact on phases

- **Phase 2 (Ingest):** unchanged in scope; the add-on skeleton (`config.yaml`/`Dockerfile`/`run.sh` + a no-op fast loop that just logs) can be stood up here to validate the Supervisor proxy + MQTT wiring before any modeling exists.
- **Phase 4 (Predict):** the fast-loop inference service and nightly retrain land inside this add-on; `min_events_for_ml` gates the threshold→ML transition (§5) via an option, no rebuild required. See Addendum D for what is actually built vs. what is still waiting on real storms.
- **Build/CI:** local-add-on iteration needs no registry; when promoting to a Git add-on repo, reuse the existing ESPHome-fleet GitHub Actions pattern (§8) to lint (`config.yaml`) and build the image per push.

## Addendum B — Flood-watch dashboard + on-demand controls

The headless add-on (no `ingress`/`ports`) is driven and observed entirely over MQTT, so a
single Lovelace dashboard doubles as an operations console during the data-collection phase.
Implemented ahead of its Phase-5 slot because watching ingestion and manually kicking the
pipeline is most useful *now*. Stock cards + the Prism theme; no custom frontend yet (§8).

### B.1 MQTT interface (add-on ⇄ HA)

- **Commands** — HA → add-on, non-retained, `creek/cmd/<name>`:
  `run_inference`, `retrain`, `promote`, `rollback`, `annotate`. The add-on keys off the
  trailing topic segment; the payload is ignored for the first four (buttons, empty
  payload) but is the whole command for `annotate` — the note text typed into the
  dashboard's "Annotate Latest Storm" text box, written onto the most recent *closed*
  storm event (`StormLog.latest_closed`, spec §7 Phase 3) rather than simply the newest
  row, since a second storm may already be open by the time the first gets annotated.
  Every command executes on the single loop thread — drained
  between/within the fast-loop sleep, so no two tasks overlap and a press is honored within
  a few seconds.
- **Status** — add-on → HA, retained JSON:
  `creek/status/pipeline` (`state`, `task`, `last_inference_at`, `last_nightly_at`,
  `last_error`), `creek/status/registry` (active/candidate versions + metrics + history,
  `event_count`), and `creek/status/command_result` (echo of each command's outcome,
  non-retained). Outputs `creek/flood_probability`, `predicted_crest`, `lag_estimate`,
  `model_health`, and `creek/alert_tier` (tier + label, §6). Timestamps are tz-aware ISO.
- **Availability** — `creek/status/availability` online/offline, backed by the MQTT LWT, so
  the discovered entities go *unavailable* when the add-on stops.

### B.1a Auto-provisioning via MQTT Discovery

The add-on publishes retained MQTT-discovery configs
(`homeassistant/<component>/rate_of_rise/<slug>/config`) for all of its `creek_*` sensors
and command buttons, grouped under a **Rate of Rise** device. HA creates/updates
them with no package or `configuration.yaml` edit, and they re-publish on every reconnect so
they track add-on updates. This supersedes the earlier "define them in a HA package" approach.
Because each entity carries a `device` block, HA prefixes the device name when minting entity
IDs — `sensor.rate_of_rise_creek_flood_probability`.

The soil-moisture mean, the ponding flag and the sensor-fault watchdogs were migrated here
too: the add-on already computed the first two, and it can see source liveness and input
freshness that an HA template cannot. `creek_warning.yaml` is down to the two things that
must stay HA-side — the tier notification automation (it calls `persistent_notification` and
the companion-app notify services) and the add-on's own liveness watchdog, since a service
cannot report its own death. That automation now covers the first two rungs of §6's
escalation ladder: a UI notification at every tier, and a critical push — Android
`alarm_stream`, audible through Do Not Disturb — at or above a configurable tier,
defaulting to 2 (Watch) because 3-4 cannot fire until the creek gauge is mounted. TTS and
any wider wake-the-house action remain unbuilt pending §8's dry-run requirement.

### B.2 Model registry (`/data/models/registry.json`)

`{active, candidate, history[], event_count}` with a `metrics` dict per entry. `promote()`
moves candidate→active (the outgoing state pushed to `history`); `rollback()` restores the
most recent history entry and keeps the demoted model as the new candidate. The artifact
behind a version is xgboost JSON plus a `.meta.json` sidecar, not the `.pkl` §4/A.7
originally specified — see Addendum D for why — and `model.py` loads it live, re-checking
`active_version` on every `predict()`.

Two properties this doc did not originally require, both added after the first real
promotion went wrong (0.20.2):

- **"No model active" is a recordable state, not the absence of one.** `promote()` pushes
  the outgoing state to `history` even when nothing was active, and a history entry with a
  null version restores the threshold estimate. Otherwise the first promotion — the one
  with the least evidence behind it — is the only one that can never be rolled back.
  `rollback()` also falls back to the threshold when a model is active with empty history,
  which recovers registries written before this.
- **An unvalidated promotion warns.** `registry.warning()` is non-None while the active
  model's metrics carry no `roc_auc`, meaning no held-out split could score it. It leads
  the command result and is published as `active_validated` in `snapshot()`. It does not
  block: a promoted model raises Tier 3/4 on its own, so the operator is told what they
  are doing, not prevented from doing it.

### B.3 HA entities & dashboard

Superseded by §B.1a: the MQTT sensors for every output/status topic, the four command
buttons, `sensor.creek_alert_tier` (§6), and the sensor-fault "stale" watchdogs (§4) are
auto-provisioned by the add-on itself (`app/discovery.py`) rather than defined in a
`ha-packages/` file — `ha-packages/creek_modeling.yaml` was removed once MQTT Discovery
replaced it (0.3.0). `ha-packages/creek_warning.yaml` is what remains, for the two things
that genuinely cannot live in the add-on. `dashboards/creek_flood_watch.yaml` presents two
views: a glanceable **household** view (tier + probability gauge + creek trend + plain-language
status) and an **operator** view (controls, pipeline/model status, ingestion-health with
staleness, and a candidate-vs-active model review).

### B.4 Later (optional)

An `ingress` log-viewer panel could be added in Phase 5 for raw log tailing; the MQTT
command/status path above stays the primary interface.

## Addendum C — Forecast/upstream ingestion (in the add-on)

Resolves how the §5 features beyond stage/soil are collected. Decision: the add-on fetches
them directly (Python) and publishes each as an MQTT-discovery sensor, rather than HA `rest:`
sensors — consistent with Addendum B (versioned, unit-testable with mocked HTTP,
auto-provisioned, secrets stay in add-on options). These are independent of the creek gauge,
so they proceed while the water-level sensor is pending and enable forecast-based Tiers 0/1.

### C.1 Architecture

`app/sources/` — one module per source, each returning `{feature: float|None}`; a coordinator
merges them into the feature row and respects a per-source refresh interval with last-good
caching (a source that errors returns its cached value / `None`, never crashing the loop).
All rainfall features are normalized to **inches**. The merged features are published on
`creek/features` (one retained JSON) and written into the widened `FeatureRow`/dataset; each
is an MQTT-discovery sensor under the *Rate of Rise* device. Location (lat/lon) is
read once from HA `/api/config` — no new option.

### C.2 Sources (delivered in two slices)

- **2a — on-site rain + NWS QPF:**
  - `rain.py` — rolling accumulator: samples `onsite_rain_rate_entity` each fast loop,
    integrates rate×Δt into a 72 h ring persisted under `/data/state/`, and reports
    `rain_{1,3,6,24,72}h_in`. Builds up over the first 72 h from a cold start.
  - `nws.py` — `api.weather.gov` `points/{lat},{lon}` → `forecastGridData` →
    `quantitativePrecipitation` (mm, ISO-interval values); pro-rated into `qpf_6h_in` /
    `qpf_24h_in`. No key; requires a `User-Agent`. Refresh ~15 min.
    Proration runs over each interval's *remaining* time, so the interval already in
    progress contributes its full forecast rather than a share scaled by how much of it
    has elapsed. BGM issues this grid in 6-hour blocks, which makes that interval most of
    a 6 h forecast; prorating it over its full span assumes the un-elapsed remainder has
    already fallen, and decays the number toward zero as a forecast storm approaches.
    Rain that really has fallen is not lost — the on-site gauge measures it as `rain_*_in`.
    **Note the standing limitation:** gridded QPF cannot resolve convection at all. A
    pop-up thunderstorm reads near zero here no matter how it is prorated, so QPF is a
    frontal-rain signal and the on-site rain rate is the only nowcast.
- **2b — upstream + model (done):** `wu.py` (Weather Underground PWS upstream accumulations
  via the shared accumulator, `wu_api_key` + `upstream_pws_ids`) and `nwm.py` (NWPS reach
  short-range streamflow forecast — near-term + peak discharge, `nwm_reach_id`).
- **2c — downstream observations (done):** `usgs.py` (NWIS instantaneous values for the two
  downstream gauges named in §1 — gage height, discharge, and 3 h rise each; free, no key).
  A larger basin with a longer lag, so *not* a creek-level proxy; its value is being the only
  **observed** rainfall→response record available before the creek node exists, which lets the
  Phase-3 lag/response work start against real hydrographs instead of waiting on hardware.

- **2d — NWS alert products (done):** `alerts.py` (active Flood Watch / Flood Warning /
  Flash Flood Warning covering the site, by point rather than county zone). These impose a
  *floor* on the alert tier per §6 — a forecaster issuing a product knows things our
  instruments do not, which matters most while the creek gauge is missing.
- **2e — SNODAS snowpack (done):** `snodas.py` (snow water equivalent for the site's grid
  cell, read straight from the gridded masked product since NOHRSC exposes no point API).
  Combined with temperature into the rain-on-snow flag §1 calls out as a major regional driver.
- **2f — Antecedent Precipitation Index (done):** `apindex.py`, an exponentially-decaying
  rainfall memory riding on the on-site rain samples. Complements the two WH51 probes with a
  basin-wide view of how wet the ground already is.
- **2g — NEXRAD storm-cell tracks (done):** `radar_cells.py` (`<nexrad site>`'s Level 3 storm-attribute
  table via the Iowa Environmental Mesonet CSV service; free, no key; per volume scan,
  ~4–6 min in precip mode). Closes the geometry gap 2a/2b cannot: storms here typically
  arrive from the W/NW while the upstream PWS corridor lies to the *SE*, so a convective
  cell on the dominant track reaches the house **before** any upstream gauge sees rain —
  and 2a's own note concedes gridded QPF cannot resolve convection at all. Each intense
  cell (≥40 dBZ) is tested for closest approach against the site using its SCIT motion
  vector (`DRCT` is direction-*from*, verified empirically against 45 live storm tracks);
  a cell passing within 4 mi inside 90 min is a threat, and one inside 45 min raises the
  Watch tier. Features: `radar_cells_tracked`, `radar_threat_cells`,
  `radar_threat_eta_min`, `radar_threat_max_dbz`. SCIT tracks only discrete cells —
  stratiform shields produce no rows, which is fine: QPF handles those.

- **2h — WPC Excessive Rainfall Outlook (done):** `ero.py` (the Weather Prediction
  Center's day 1-3 categorical flood-risk areas — Marginal / Slight / Moderate / High —
  as a point query via IEM's `outlook_by_point` service; free, no key, no shapefile
  parsing). This is the only input in the system that grades forecast rain against
  **flash flood guidance** — against what the ground can currently absorb — rather than
  reporting rain in inches. Everything else answers "how much water"; the ERO answers
  "is that much water a problem *here, today*", folding in antecedent conditions and
  soil state that our two probes cannot see basin-wide.

  It also completes the horizon ladder. The ERO is the **day**-scale signal, issued
  before anything exists on radar; 2g's cell tracks are the **hour**-scale one; the
  on-site gauge and upstream PWS are the **now**-scale one. Day 1 contributes to the
  Advisory tier (Moderate+ alone, Slight over already-wet ground); days 2-3 are carried
  as model features only, since a High risk 48 h out is not something to act on tonight.

  An absent risk area is recorded as 0.0 — WPC looked and drew nothing, which is a real
  low-risk forecast — while an unreadable product is None, so the watchdog can tell a
  quiet day from a broken feed. Verified against the live service on 2026-08-02, which
  returned a Day-1 Slight over the site during the storm that had raised the Watch tier.

- **2i — Google Flood Forecasting status (done):** `google_floods.py`
  (`gauges:searchGaugesByArea` over a 25 mi box to find the gauges Google models near the
  site, then `floodStatus:queryLatestFloodStatusByGaugeIds` for the nearest ten; needs a
  Google Cloud project, the API enabled, and `google_floods_api_key`). Non-quality-verified
  and virtual HydroBASINS gauges are deliberately included — on a creek this small they are
  the only plausible candidates, and they are exactly what the default filter drops.

  This is the only input in the system that is a forecast of **flooding** rather than of
  weather. QPF says how much rain; the ERO says whether that rain exceeds flash-flood
  guidance somewhere in a multi-county risk area; NWM gives raw discharge for one reach
  with no notion of what is high for it. Google has already graded each reach against that
  reach's own warning / danger / extreme thresholds.

  It is also the only source that can be correctly enabled and still report nothing.
  Google gauges no reach as small as this creek, so a status here is about the
  neighbouring river network — the same family as 2c's USGS gauges, a regional answer to
  the same rain on a bigger and slower system. Features: `google_flood_severity`
  (0 no flooding · 1 above normal · 2 severe · 3 extreme), `google_flood_trend`
  (+1 rising / 0 steady / -1 falling), `google_flood_gauge_mi` and `google_flood_gauges`.
  The first three always describe the same gauge — worst severity, nearest first among
  equals — so they read as one forecast. A count of 0 means Google models nothing within
  25 mi, which is a real reading, not a fault, and is the answer to open question #2.

  Because the reach is never ours, this source is capped at **Tier 2 Watch**: an
  above-normal river within 15 mi is an Advisory, severe or extreme is a Watch, and
  Warning / Emergency stay reserved for the creek's own instrument. The 15 mi tier radius
  is tighter than the 25 mi search radius on purpose — the wider box is the right width
  for a model feature, not for an alarm.

  Not ingested: `gaugeModels.batchGet` thresholds and `gauges.queryGaugeForecasts` values,
  which would replace the 4-step ladder with a continuous "fraction of the way to warning
  level" (open question #2's residual); and `inundationMapSet`, a satellite-derived
  inundation product for ungauged basins outside the US. `flashFloods` is a separate
  endpoint from both and *is* ingested — see 2j.

- **2j — Google Flash Flood polygon containment (done):** `google_floods.py`, same key
  as 2i. `flashFloods:search` (filtered only by country code — no lat/lon filter exists
  at this API's level) returns every currently active or forecast flash-flood event
  nationally as polygon IDs, not coordinates or a severity field. Each event's
  `event_polygon_id` (the union of its likely- and highly-likely-affected areas) is
  resolved via `serializedPolygons/{id}` to KML and checked against the site's own
  lat/lon with a hand-rolled point-in-polygon test (`app/sources/kml_geometry.py`) —
  outside the union means outside both, so only a site inside it pays for a second fetch
  to tell "likely" from "highly likely."

  This is the answer to open question #2's remaining half: unlike 2i, which reads a
  *neighbouring gauge*, this reads the site itself. Features: `google_flash_flood_likely`
  and `google_flash_flood_highly_likely` (0/1, mutually describing the worst event
  overlapping the site) and `google_flash_flood_events` (count of active national events
  the site falls inside — almost always 0). Same Tier 2 Watch ceiling as 2i, for the same
  reason: still a Google model forecast, not the creek's own instrument.

Phase 2 ingest is complete.

### C.3 Consumption

Ingested features are recorded to the dataset, published on `creek/features`, and — as of
slice 2c — consumed by the tier logic. `app/tiers.py` evaluates §6 against the whole feature
row and emits `0` All-clear · `1` Advisory · `2` Watch · `3` Warning · `4` Emergency (the §6
table plus the explicit all-clear state §6 also calls for), with the reasons that fired.

The design point: **Advisory and Watch are gauge-independent.** Advisory comes from QPF plus
antecedent soil moisture, Watch from upstream/on-site rain accumulation — both already
flowing. So the system issues real warnings during the wait for the SEN0676, while Warning
and Emergency (stage, rate-of-rise) stay dormant until the creek node reports. Feeding these
features into the *model* remains Phase 4, behind the same interfaces.

## Addendum D — Model training pipeline (Phase 4)

`app/train.py` implements §4/§5/§7's "recalibrate/retrain, version model artifact, log
skill metrics". **Code-complete and unit-tested against synthetic storms
(`tests/test_train.py`, `tests/test_model.py`); has never seen real data.** Two gates sit
in front of it, both intentional:

1. `min_events_for_ml` (§5) — `_nightly_batch` (`__main__.py`) does not call `train()` at
   all below this many captured storms (`storms.count()`).
2. Past that gate, `train()` refuses to fit if the label has fewer than
   `MIN_POSITIVE_LABELS` positives. The label is "stage or rate-of-rise reaches Warning
   within +3 h" (`tiers.WARNING_STAGE_FT` / `WARNING_RATE_OF_RISE_IN_MIN`, reused directly
   rather than duplicated), and both of those are `None` until the SEN0676 is mounted — so
   in the live system today this gate never opens, correctly. A classifier fit on zero
   positive examples has learned "always predict no," which is a worse answer than the
   threshold estimate with more confidence behind it, not a better one.

### D.1 What is built

- **Label:** forward-window Warning exceedance at a single **+3 h** horizon — the largest
  of the three §5 asks for, kept alone because three untested horizons cost three times the
  surface for no way to validate any of them before real storms exist.
- **Model:** xgboost binary classifier (`binary:logistic`), chosen concretely because it
  handles missing feature values natively — most rows have gaps today (Google Floods needs
  a key *and* a gauge Google models nearby, WU needs both keys configured, stage and
  rate-of-rise are always `None` pre-hardware),
  and a hand-rolled model would need an imputation strategy invented for a missingness
  pattern that is not yet known. `requirements.txt` already carries it for this
  (Addendum A.2); the Dockerfile's `libgomp1` exists only to support it.
- **Split:** chronological (most recent `TEST_FRACTION` by time, not row count), with an
  embargo of one horizon-width on each side of the split so a storm straddling the boundary
  cannot leak between train and test.
- **Class imbalance:** `scale_pos_weight`, not resampling — flood events are rare by
  construction (that is why storms take months to accumulate), and up-weighting costs
  nothing extra to store or compute.
- **Skill metrics** (§4/§7): hit rate (recall), false-alarm rate, ROC AUC, and mean lead
  time — the gap between a correct positive prediction and the actual Warning-condition
  onset it preceded, walked forward per row rather than assumed. Reported as `None` rather
  than a misleading `0.0` when the test split lands single-class, which is the norm rather
  than the exception at this stage.
- **Serialization:** xgboost's native JSON (`Booster.save_model`) plus a `.meta.json`
  sidecar for feature-column order, **not** the `.pkl` this doc originally specified (§4/A.7)
  — deliberately: a pickle ties the artifact to the exact xgboost build that wrote it and
  executes arbitrary code on load, which is a poor trade for a format with exactly one
  consumer. `ModelRegistry` (`registry.py`) is untouched by any of this — it stores
  version/metrics JSON only, exactly as designed ("testable without a broker, HA, or a
  trained model"), and `train()` never writes to `active`, only `set_candidate`. Promoting a
  candidate to active is the dashboard's **Promote** button, a human decision — §4's
  "post-storm: manual review + model promotion step."
- **Not built:** `predicted_crest_ft` (stage regression). With no creek gauge there has
  never been a real `stage_ft` sample to regress against; building one now would exercise
  library API surface, not model anything. Revisit once real stage data exists.

### D.2 A gap this closed: promote/rollback now take effect live

Before this addendum, `Model._load_promoted` was a stub that always returned nothing, so
whether the dashboard's Promote/Rollback buttons would actually change live inference was
never tested and, on inspection, would not have: `Model` loaded its artifact once at
construction, while `_promote`/`_rollback` (`__main__.py`) only ever mutate the shared
`ModelRegistry` — a promotion would have silently done nothing until the add-on restarted.
`Model` now re-checks `registry.active_version` on every `predict()` call (a cheap string
compare) and reloads when it changes, so a promotion or rollback is live on its next fast
loop, no restart required. Covered by `tests/test_model.py`, including the corrupted/missing
artifact case: the registry naming a version does not guarantee the file behind it is
loadable, and `Model` fails open to the threshold estimate rather than crashing the loop.
