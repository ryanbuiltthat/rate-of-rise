# Storm Runbook

What to do when a storm hits. Checklist form — meant to be readable on a phone at 2 am.

> **ewfa now pushes to your phone — but do not treat it as your alarm clock.** Tier 2
> (Watch) and above send a critical push on Android's alarm stream; below that, an
> ordinary notification. Getting it through **Do Not Disturb needs a one-time permission
> granted on each phone by hand** — it is not Android's default and no config here can
> set it. Until you have run `script.creek_alert_test` with DND on and actually heard it,
> assume it will not wake you. Thresholds are also still uncalibrated.
> **NWS/NOAA is still your real alerting path.** This is a data-collection aid.

> **Tier 4 cannot fire yet.** The SEN0676 radar isn't mounted, so tiers 3–4 are dormant
> and a dangerous storm tops out at Watch (unless a Flash Flood Warning floors it to
> Warning). A low tier is not reassurance.

---

## Before / early in the storm — 30 seconds

- [ ] Open **Creek Flood Watch → Operator → Watchdogs**.
- [ ] All watchdogs off? Good. If **Modeling service stale** or **Upstream PWS missing** is
      on, fix it now — a storm recorded with dark sources is a wasted storm, and the ML
      gate wants 10 of them.
- [ ] Don't act on the tier itself. Treat it as *go look*, not an alarm.
- [ ] **Ignore QPF for thunderstorms.** Gridded forecast, 6-hour blocks — it cannot resolve
      convection and will read near zero during a pop-up storm. That is expected, not a
      fault. Watch **rain rate / rain 1h** (on-site, every 5 min) instead; that is the only
      nowcast. QPF is for frontal rain.
- [ ] If it's raining and **rain rate is also flat**, that's a real fault — Ecowitt
      ingestion, not NWS. Check the Ingestion health card.

## During — you are the gauge

With no creek sensor, your eyes are the only record of the response side. **Note clock
times**, roughly is fine:

- [ ] Creek visibly starts rising — **time**
- [ ] Creek appears to crest — **time**
- [ ] High-water mark (photo against a fixed reference — rock, post, tree)
- [ ] Culvert running full? — **time**
- [ ] Basement: dry / damp / water — **time**

Time-stamped phone photos count as all of the above.

- [ ] **While the ground is soaked**, write down both soil probes at peak wetness:
      `..._soil_moisture_willow` (house) and `..._soil_moisture_field` (creek).
      These are the "saturated" endpoints for open question #7. Grab the "dry" values in
      the next dry spell.

## After — next day

Storm events don't close until the quiet window has elapsed (6 h by default —
`storm_quiet_hours` in the add-on options), so do this the following day.

- [ ] **Operator tab → Storms & lag → "Ready to annotate"** — confirm it names the storm
      you watched (`Storm #<n>`), then type the times from *During* into **"↳ notes"** and
      hit enter. That's it — no terminal, no SQL.

  It targets the most recent **closed** storm, not just the newest row, so it's still
  right even if a second storm has already opened since. If it says `none yet`, nothing
  has closed — wait out the quiet window, or check Storm In Progress.

  If a storm *never* closes, the quiet clock is being held open by a rain signal that
  never reaches zero. Check the history of **Rain (1 h)** and **Upstream rain (1 h)**
  across a dry stretch: a stuck upstream PWS reporting a phantom rain rate holds every
  storm open forever, since either gauge alone can keep the event alive. Shortening
  `storm_quiet_hours` will not fix that — the clock never starts.

- [ ] **Only if you need something the text box can't do** — annotating an *older*
      un-annotated storm, or correcting a note already saved — use SQL directly. From the
      **SSH & Web Terminal** add-on, no `docker exec`, no protection-mode toggle:

```sh
sqlite3 /share/creek_modeling/events.sqlite \
  "SELECT id, datetime(started_ts,'unixepoch','localtime') AS started, ended_ts, notes
     FROM storm_events ORDER BY id DESC LIMIT 5;"

sqlite3 /share/creek_modeling/events.sqlite \
  "UPDATE storm_events SET notes='crest ~40min after upstream peak; culvert full; basement dry'
     WHERE id=3;"
```

Same path over Samba if you'd rather use a GUI SQLite browser: `\\<ha-host>\share\creek_modeling\`.

Either way, put the times from *During* in the notes. That's what calibrates the lag.

- [ ] Judge the tiers: did it fire? too early, too late, not at all? Note it — every
      threshold in `creek_modeling/app/tiers.py` and `app/storms.py` is a placeholder, and
      an observed storm is the only thing that can move them off literature defaults.
- [ ] Check **Storms recorded** on the Operator tab against `min_events_for_ml` (10).

## Nothing to press

- **Run inference now** only skips the wait for the next 5-minute tick.
- **Retrain / Promote / Rollback** do nothing useful until Phase 4.
- The nightly batch (3 am) rolls up the dataset and refits the lag on its own.

---

## What runs without you

| | |
|---|---|
| Storm detection | opens at 0.10 in/h rain (on-site or upstream), closes after 6 h quiet — all three tunable (`storm_start_rain_1h_in`, `storm_continue_rain_1h_in`, `storm_quiet_hours`) |
| Event log | peaks + onset conditions written to `/share/creek_modeling/events.sqlite`, survives restarts |
| Tier evaluation | every 5 min; active NWS products floor the tier |
| Dataset | JSONL parts per fast loop, consolidated to Parquet nightly |

## Related

- [open-questions.md](./open-questions.md) — #5 (datum), #7 (soil calibration), #8/#9/#10
  (thresholds) are all things a storm helps answer
- [creek-flood-warning-spec.md](../creek-flood-warning-spec.md) — §6 alert tiers, §7 phases
