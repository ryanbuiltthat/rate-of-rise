# Storm Runbook

What to do when a storm hits. Checklist form — meant to be readable on a phone at 2 am.

> **Rate of Rise now pushes to your phone — but do not treat it as your alarm clock.** Tier 2
> (Watch) and above send a critical push on Android's alarm stream; below that, an
> ordinary notification. Getting it through **Do Not Disturb needs a one-time permission
> granted on each phone by hand** — it is not Android's default and no config here can
> set it. Until you have run `script.creek_alert_test` with DND on and actually heard it,
> assume it will not wake you. Thresholds are also still uncalibrated.
> **NWS/NOAA is still your real alerting path.** This is a data-collection aid.

> **The creek node is live (deployed 2026-09-19).** Tiers 3–4 (Warning/Emergency) now fire
> off real stage and rate-of-rise, not just forecast/upstream signals — but the stage and
> Warning/Emergency thresholds are still placeholders (open question #8), unlike the
> surveyed geometry they're set against. A quiet tier is still not reassurance; it may
> also mean the radio link is down rather than the creek being calm — see *Before*, below.

---

## Before / early in the storm — 30 seconds

- [ ] **Creek Alert must be on** (Settings → Automations → *Creek Alert*). Since 0.23.0 it
      re-arms itself on restart and after 2 h off, but check. To quiet the phones while
      working at the creek, use **Operator → Controls → Pause phone alerts (2 h)** — never
      switch the automation off. The pause ends by itself.
- [ ] Data problems now **push to both phones** (telemetry stale, radar fault, stage
      stale / frozen / implausible, rain sources, service stale) — critically if a storm is
      open and the gauge is blind. If one arrives, the tiers above it are not trustworthy
      until it clears.
- [ ] Open **Creek Flood Watch → Operator → Watchdogs**.
- [ ] All watchdogs off? Good. If **Modeling service stale** or **Upstream PWS missing** is
      on, fix it now — a storm recorded with dark sources is a wasted storm, and the ML
      gate wants 10 of them.
- [ ] Check **Ingestion health → Creek node link**. If it's OFF (or **Creek stage stale** is
      ON), the radio link is down: the gateway keeps serving the last stage the node sent
      rather than blanking it, so the number on screen may be stale, not current. Tiers 3–4
      go dormant for real (not just quiet) until it reconnects. **Stage reading age** says
      how stale. This is exactly the failure mode the offline-pattern firmware fixes
      target, so it should be rare — but the storm is the test of that, not the bench.
- [ ] Don't act on the tier itself. Treat it as *go look*, not an alarm.
- [ ] **Ignore QPF for thunderstorms.** Gridded forecast, 6-hour blocks — it cannot resolve
      convection and will read near zero during a pop-up storm. That is expected, not a
      fault. Watch **rain rate / rain 1h** (on-site, every 5 min) instead; that is the only
      nowcast. QPF is for frontal rain.
- [ ] If it's raining and **rain rate is also flat**, that's a real fault — Ecowitt
      ingestion, not NWS. Check the Ingestion health card.

## During — the sensor is the gauge now; you're the check on it

The creek node reports depth and rate of rise every 60 s (**Creek Flood Watch → Creek
Level (24 h)**, and **Operator → Ingestion health**). That's the record now — but it's a
radar reading a water surface, and it can be wrong in ways your eyes will catch faster
than a watchdog will: floating debris under the beam, spray or foam in a high flow, a
reading that stops moving because the *link* dropped rather than the creek holding steady.
**Note clock times**, roughly is fine:

- [ ] Creek visibly starts rising — **time** (compare against when `sensor.creek_gateway_creek_depth`
      actually moved — a real gap between the two, beyond the node's 60 s cadence, is worth
      a note)
- [ ] Creek appears to crest — **time**, and roughly how high (compare to the depth reading
      and to **Predicted crest** on the Now card)
- [ ] High-water mark (photo against a fixed reference — rock, post, tree). This is still
      the best way to catch the sensor reading low or high — cross-check it against the
      depth reading at the same moment.
- [ ] Culvert running full? — **time**
- [ ] Basement: dry / damp / water — **time**
- [ ] Did a Warning or Emergency tier fire? Note whether it matched what you were seeing at
      the same clock time — this is now real calibration data for #8 (stage-based
      thresholds), not just a placeholder to eyeball later.

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
sqlite3 /share/rate_of_rise/events.sqlite \
  "SELECT id, datetime(started_ts,'unixepoch','localtime') AS started, ended_ts, notes
     FROM storm_events ORDER BY id DESC LIMIT 5;"

sqlite3 /share/rate_of_rise/events.sqlite \
  "UPDATE storm_events SET notes='crest ~40min after upstream peak; culvert full; basement dry'
     WHERE id=3;"
```

Same path over Samba if you'd rather use a GUI SQLite browser: `\\<ha-host>\share\rate_of_rise\`.

Either way, put the times from *During* in the notes. That's what calibrates the lag.

- [ ] Judge the tiers: did it fire? too early, too late, not at all? With the creek node
      live, this now includes Warning/Emergency (stage-based), not just Advisory/Watch —
      pull up **Creek Level (24 h)** against the times you logged and check whether the
      24 in / 30 in thresholds tracked what actually happened at the creek. Note it — every
      threshold in `rate_of_rise/app/tiers.py` and `app/storms.py` is a placeholder, and
      an observed storm is the only thing that can move them off literature defaults.
- [ ] If **Creek node link** went off at any point during the storm, note the window — a
      stage-based tier is dormant for that window regardless of what the creek did, and
      that gap belongs in the notes alongside the crest time.
- [ ] Check **Storms recorded** on the Operator tab against `min_events_for_ml` (10).
- [ ] The crest at full resolution is in `\\<ha-host>\share\rate_of_rise\stage\<date>.csv`
      (`reading_ts` is unix seconds). Compare it with your clock times — it is the record
      the calibration of #8 should be done against, not the 5-minute dataset.

## Nothing to press during the storm

- **Run inference now** only skips the wait for the next 5-minute tick.
- The nightly batch (at `nightly_retrain_hour`) rolls up the dataset and refits the lag on
  its own.
- **ML is in shadow** unless the add-on option `ml_drives_alerts` is on (default off): the
  tiers use the threshold estimate, and *ML shadow probability* on the Flood Watch header
  and Operator tab shows what the model says. Note it against what the creek does — that is
  the evidence for ever letting it drive the alarm.
- **Creek stage implausible** means the gauge jumped further than the creek can move and
  the tiers are ignoring it. Go and look. If it holds 30 minutes the add-on believes it.
- The storm's high-resolution record (every stage reading, ~10 s) is being written to
  `/share/rate_of_rise/stage/` by the add-on itself, so it survives an HA recorder stall and
  HA's 10-day history retention.
- **Retrain / Promote / Rollback** are live now that Phase 4 has landed, and none of them
  is a storm-time action — see below. Retrain in particular reads the whole dataset and
  fits a model; do it after, not while you are watching the creek.

## Retrain / Promote / Rollback — after the storm, not during

The storm log cleared `min_events_for_ml`, so **Retrain** now produces a real candidate
instead of skipping. What it produces on a short record is usually a model whose held-out
split contains no Warning-tier crossings at all, and a split with no positives cannot
score anything — hit rate, false-alarm rate and AUC all come back undefined.

Since 0.23.0 a promoted model is advisory by default: with `ml_drives_alerts` off it runs in
shadow and the tiers use the threshold estimate. Turn that option on and its probability
alone raises **Tier 3 at 50% and Tier 4 at 80%** — so doing that for an unscored model hands
the alarm to something nothing has checked. "Validated" now also requires the model to have
caught at least one held-out positive.

- **Promote** will still activate such a candidate — the judgement is yours — but it says
  so at the press (the caveat leads **Last Command**) and keeps saying so: the Active
  Model sensor carries `active_validated: false` for as long as that model is active, and
  the Model review card shows a banner.
- **Rollback** undoes it. With no earlier model to return to it restores the threshold
  estimate, so backing out is always available — including immediately after the very
  first promotion.
- Check the candidate's metrics on the Operator tab **before** promoting. `roc_auc`
  present means a split could score it; a bare `note` about a single-class split means
  nothing could.

If a promoted model starts producing tiers that do not match what you can see at the
creek, press **Rollback** and note it — that observation is worth more than the model.

---

## What runs without you

| | |
|---|---|
| Creek node | reports stage + rate of rise every 60 s over the RFM69 link; a dropout freezes the reading rather than blanking it, so **Creek node link** / **Stage reading age** are what tell you it's stale |
| Storm detection | opens at 0.10 in/h rain (on-site or upstream), closes after 6 h quiet — all three tunable (`storm_start_rain_1h_in`, `storm_continue_rain_1h_in`, `storm_quiet_hours`) |
| Event log | peaks + onset conditions written to `/share/rate_of_rise/events.sqlite`, survives restarts |
| Tier evaluation | every 5 min; active NWS products floor the tier |
| Dataset | JSONL parts per fast loop, consolidated to Parquet nightly |

## Related

- [open-questions.md](./open-questions.md) — #7 (soil calibration) and #8/#9/#10
  (thresholds) are all things a storm helps answer. #8's stage-based half was blocked on
  #5 (the datum); #5 is resolved and the node is deployed, so a storm now calibrates
  Warning/Emergency directly, not just Advisory/Watch.
- [creek-flood-warning-spec.md](../creek-flood-warning-spec.md) — §6 alert tiers, §7 phases
