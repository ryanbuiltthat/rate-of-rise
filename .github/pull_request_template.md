<!--
Keep the sections that apply and delete the ones that don't — a short, honest PR beats a
fully-populated template. The comments are prompts, not text to leave in.
-->

## What changed, and why

<!--
Lead with the reason. For a fix, the root cause is the point: what the code actually did,
not just the symptom. Paste the error or the log line that started it.

Past entries in rate_of_rise/CHANGELOG.md are the house style — "the gateway does not
blank the stage sensor, it just stops updating it, so HA keeps serving the last value",
not "fixed stale stage bug".
-->

## Effect on alerting

<!--
This system's job is to warn before water arrives, so say what this does to that. A false
Tier 3 trains the operator to ignore the alarm; a missed one is the failure the project
exists to prevent. Both of this repo's first two fixes were false Tier 3s.

Call out any change to: tier thresholds or the rules in tiers.py, flood probability or
the model, features feeding either, the storm log, or the NWS floors. Say "none — this
does not touch the alerting path" when that's the case, so a reviewer knows you checked
rather than didn't think about it.
-->

## What the operator has to do

<!--
Anything beyond updating the add-on. For example:
- re-import a dashboard, or restart HA to pick up a package change
- new/renamed MQTT discovery entities (old ones linger until deleted by hand)
- a new option in config.yaml that needs a value before it takes effect
- flashing the creek node or the gateway
- a one-time recovery step (press Rollback, delete a stale file)
Write "nothing — update the add-on" if that's genuinely all.
-->

## Testing

<!--
What you actually ran, and what it proved. For a fix, reproducing the original failure
first is worth more than the passing run afterwards — say whether you did.

  for t in rate_of_rise/tests/test_*.py; do python "$t"; done

Anything exercised against real hardware, a live storm, or replayed field data is worth
naming explicitly — most of this repo can only be tested against synthetic fixtures, so
the distinction matters.
-->

---

- [ ] **Version bumped** in `rate_of_rise/config.yaml` **with a matching `## <version>`
      section in `rate_of_rise/CHANGELOG.md`** — CI fails without both, and the bump is
      what surfaces the Update button in the add-on store. Skip only for changes that
      ship nothing to the add-on (docs, CI, firmware-only).
- [ ] Test suite passes, and new behaviour has a test that would have caught the bug.
- [ ] No site details: no exact coordinates, creek or place names, station IDs, API keys,
      or WU/PWS identifiers. Site specifics belong in add-on options, not the repo.
- [ ] Firmware changes note which board, and whether existing hardware must be reflashed.
