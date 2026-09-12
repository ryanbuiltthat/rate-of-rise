"""The tier-change alert automation in the HA package.

This automation is the one piece of the system that only ever runs unattended, at the
worst possible moment, on a phone that is face-down in a dark room. Nothing exercises it
in normal use, so the parts that would silently render it useless -- no targets, a
critical floor set above any tier that can actually fire, a push that does not route
through the alarm stream -- are pinned here instead.

Two rounds got this wrong before landing on the current shape, both worth remembering:

  0.14.1 called `action: "{{ repeat.item }}"` against a list of `notify.<name>` strings,
  assuming companion-app phones have a fixed, guessable notify *service* name. They do
  not -- it failed with "unknown action: notify.ryanphone".

  0.14.2 switched to a device action (device_id / domain: mobile_app / type: notify) but
  kept the repeat/for_each loop, templating device_id as `"{{ repeat.item }}"`. That also
  failed, for a structural reason rather than a naming one: Home Assistant resolves a
  device action's device_id when the automation is *set up*, before any per-iteration
  template rendering happens, so the literal string was never evaluated as a template at
  all -- "Unknown device '{{ repeat.item }}'".

0.14.3 uses one explicit, non-templated action per phone (device_id is a literal value,
never a template), sharing content via a YAML anchor rather than a runtime loop. Every
test below that references "the repeat step" from earlier versions was rewritten to walk
the actual list of per-phone device actions instead.

Run: python creek_modeling/tests/test_alert_notifications.py
"""
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "ha-packages" / "creek_warning.yaml"

# Home Assistant device ids are a 32-character lowercase hex string.
DEVICE_ID = re.compile(r"^[0-9a-f]{32}$")

# Highest tier app/tiers.py can emit, and the highest reachable without the creek gauge.
MAX_TIER = 4
MAX_TIER_WITHOUT_GAUGE = 2


def automation():
    doc = yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))
    for entry in doc["automation"]:
        if entry.get("id") == "creek_tier_change":
            return entry
    raise AssertionError("creek_tier_change automation is missing from the package")


def script():
    doc = yaml.safe_load(PACKAGE.read_text(encoding="utf-8"))
    entry = (doc.get("script") or {}).get("creek_alert_test")
    if entry is None:
        raise AssertionError("creek_alert_test dry-run script is missing from the package")
    return entry


def script_steps():
    """The dry-run script's per-phone device actions."""
    steps = [s for s in script()["sequence"] if isinstance(s, dict) and "device_id" in s]
    if not steps:
        raise AssertionError("the dry-run script sends no push")
    return steps


def render_variables(to_state=None, live=None):
    """Render the automation's `variables:` block the way Home Assistant does -- in
    order, each template able to see the ones defined before it.

    `to_state=None` simulates a MANUAL run (Developer tools -> Actions ->
    automation.trigger): Home Assistant provides no `trigger` variable at all, so any
    template that reaches for `trigger.to_state` raises UndefinedError and the automation
    dies before its first action. That is not a hypothetical -- it shipped in
    0.14.1-0.14.3. `live` is what the entity reads back as, for the fallback path.

    Raises jinja2.UndefinedError if the variables cannot be built, which is the failure
    being guarded against.
    """
    live = live or {"state": "0", "label": "All-clear", "why": "nothing elevated"}
    env = jinja2.Environment()
    ctx = {
        "states": lambda _e: live["state"],
        "state_attr": lambda _e, attr: live.get(attr),
    }
    if to_state is not None:
        ctx["trigger"] = SimpleNamespace(
            to_state=SimpleNamespace(state=str(to_state["state"]), attributes=to_state),
            from_state=SimpleNamespace(state="0", attributes={}),
        )

    for key, raw in automation()["variables"].items():
        if not isinstance(raw, str) or "{{" not in raw:
            ctx[key] = raw
            continue
        out = env.from_string(raw).render(**ctx)
        # Home Assistant parses native types back out of a rendered template.
        if out in ("True", "False"):
            ctx[key] = out == "True"
        else:
            try:
                ctx[key] = int(out)
            except ValueError:
                ctx[key] = out
    return ctx


def push_steps():
    """Every per-phone companion-app action -- anything with a device_id key. Not a
    repeat block: see the module docstring for why that shape does not work here."""
    steps = [a for a in automation()["actions"] if isinstance(a, dict) and "device_id" in a]
    if not steps:
        raise AssertionError("no per-phone device-action push step in the automation")
    return steps


def test_there_is_at_least_one_notify_target():
    assert len(push_steps()) >= 1


def test_targets_are_device_ids_not_notify_service_names():
    """The regression this guards: `notify.<name>` is not a real, callable service for a
    companion-app phone -- there is no fixed name to template against, and 0.14.1 shipped
    exactly that assumption and broke ("unknown action: notify.ryanphone")."""
    for step in push_steps():
        t = str(step["device_id"])
        assert DEVICE_ID.match(t), f"{t} does not look like a device id"
        assert not t.startswith("notify."), f"{t} is a notify-service name, not a device id"


def test_device_id_is_never_templated():
    """The 0.14.2 regression: Home Assistant resolves a device action's device_id when
    the automation is set up, before any per-iteration Jinja rendering, so a templated
    value like "{{ repeat.item }}" is never evaluated -- it errors as a literal, invalid
    device id and the whole automation fails to load."""
    for step in push_steps():
        assert "{{" not in str(step["device_id"]), step["device_id"]


def test_there_is_no_repeat_loop_over_the_targets():
    """Structural guard for the same 0.14.2 regression: device actions cannot be driven
    by repeat/for_each at all, so if one reappears here it is broken again regardless of
    what device_id inside it looks like."""
    def walk(node):
        if isinstance(node, dict):
            assert "repeat" not in node, "a repeat step cannot drive per-phone device actions"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(automation()["actions"])


def test_every_target_is_notified_not_just_the_first():
    """Two phones are configured; both must have their own action, not just one."""
    ids = {step["device_id"] for step in push_steps()}
    assert len(ids) == len(push_steps()), "duplicate device_id -- one phone shadows another"
    assert len(ids) >= 2, "fewer push actions than configured phones"


def test_the_push_is_a_device_action_not_a_templated_service_call():
    """The fix itself: target each phone by device id through the mobile_app/notify
    device action, not by templating a notify.<name> service string that may not exist."""
    for step in push_steps():
        assert step["domain"] == "mobile_app"
        assert step["type"] == "notify"
        assert "action" not in step, "reverted to calling a templated service name"


def test_one_dead_target_does_not_silence_the_rest():
    """An unavailable or misconfigured device raises. Without continue_on_error on every
    block, the automation aborts there and every action after it is never run."""
    for step in push_steps():
        assert step["continue_on_error"] is True


def test_the_critical_floor_is_reachable_today():
    """Tiers 3-4 need the creek gauge, which is not mounted. A floor above 2 would mean
    no critical alert can physically fire -- the feature would look wired up and never
    make a sound."""
    floor = automation()["variables"]["critical_from_tier"]
    assert 0 <= floor <= MAX_TIER, floor
    assert floor <= MAX_TIER_WITHOUT_GAUGE, (
        f"critical_from_tier={floor} cannot fire until the creek gauge is mounted")


def test_a_critical_push_routes_through_the_alarm_stream():
    """`alarm_stream` is what carries the sound through silent, vibrate and Do Not
    Disturb. Any other channel and a Warning at 3 a.m. is a silent notification."""
    for step in push_steps():
        data = step["data"]
        assert "alarm_stream" in data["channel"], data["channel"]
        assert "is_critical" in data["channel"], "the channel must depend on the tier"
        assert data["ttl"] == 0            # past Doze batching
        assert data["priority"] == "high"


def test_the_push_replaces_rather_than_stacks():
    """A storm walks the tier up and back down repeatedly; without a constant tag each
    step leaves its own notification and the phone fills with stale alarms."""
    for step in push_steps():
        assert step["data"]["tag"] == "{{ push_tag }}"
    assert automation()["variables"]["push_tag"] == "creek_alert_tier"


def test_the_dry_run_script_exists_and_is_genuinely_critical():
    """§8 requires this be testable without waiting for a storm. The previous procedure
    -- temporarily set critical_from_tier to 0, reload, test, set it back -- put four
    steps around the thing being tested, and skipping the first silently downgrades the
    test to a non-critical all-clear that proves nothing while looking like a failure."""
    seq = script()["sequence"]
    v = seq[0]["variables"]
    assert v["is_critical"] is True
    assert v["tier"] >= 1, "a tier-0 test would send a quiet all-clear, not an alarm"


def test_the_dry_run_reaches_exactly_the_same_phones_as_a_real_alert():
    """A test that notifies a different set of phones than the alert does is not a test
    of the alert."""
    assert {s["device_id"] for s in script_steps()} == \
           {s["device_id"] for s in push_steps()}


def test_the_dry_run_sends_the_same_payload_as_a_real_alert():
    """The whole point of sharing the YAML anchor: what the test sends must be, by
    construction, what a real alert sends. Anything that drifted here would validate a
    notification nobody will ever receive."""
    real = [{k: v for k, v in s.items() if k != "device_id"} for s in push_steps()]
    test = [{k: v for k, v in s.items() if k != "device_id"} for s in script_steps()]
    assert test and real and test[0] == real[0], "dry run has drifted from the real alert"


def test_the_dry_run_does_not_clobber_a_live_alert():
    """Same tag would mean firing the test replaces a real, unacknowledged flood alert
    on the phone with a message saying no flooding is occurring."""
    assert script()["sequence"][0]["variables"]["push_tag"] != \
           automation()["variables"]["push_tag"]


def test_title_and_message_are_present_on_the_device_action():
    """Device actions carry title/message as top-level keys, not nested under `data:`
    the way a plain notify service call does -- a leftover nested `data.title` would
    silently produce a blank notification."""
    for step in push_steps():
        assert "{{ push_title }}" in step["title"]
        assert "{{ why }}" in step["message"]


def test_every_phone_gets_identical_content():
    """The anchor/merge-key sharing is a source-file convenience -- confirm it actually
    produces the same notification for every phone rather than only the first."""
    steps = push_steps()
    shared = [{k: v for k, v in s.items() if k != "device_id"} for s in steps]
    assert all(s == shared[0] for s in shared), "phones would receive different pushes"


def test_a_manual_run_builds_its_variables_instead_of_dying():
    """The 0.14.1-0.14.3 failure, and the reason three releases in a row did nothing when
    tested: a manual run (automation.trigger) has no `trigger` variable, so every
    template reaching for `trigger.to_state` raised UndefinedError while the variables
    were still being assembled -- before the first action, before even the persistent
    notification. The automation appeared to do absolutely nothing.

    A manual run is the only way to test this without waiting for a storm, which spec §8
    requires, so it has to describe live conditions rather than fall over."""
    v = render_variables(to_state=None,
                         live={"state": "2", "label": "Watch", "why": "upstream rain"})
    assert v["tier"] == 2
    assert v["label"] == "Watch"
    assert v["why"] == "upstream rain"
    assert v["is_critical"] is True
    assert "Tier 2" in v["push_title"]


def test_a_real_trigger_still_describes_the_state_that_fired_it():
    """The fallback must not cost the original race guard: reading live state when a
    trigger IS present would let the entity move on before the actions run, and the push
    would name a tier other than the one that fired. Trigger wins when it exists."""
    v = render_variables(
        to_state={"state": "4", "label": "Emergency", "why": "stage 2.60 ft"},
        live={"state": "0", "label": "All-clear", "why": "nothing elevated"})
    assert v["tier"] == 4, "fell back to live state despite having a trigger"
    assert v["label"] == "Emergency"
    assert v["why"] == "stage 2.60 ft"
    assert v["is_critical"] is True
    assert "Emergency" in v["push_title"] and "Tier 4" in v["push_title"]


def test_an_all_clear_renders_without_the_alarm():
    """Tier 0 must not come through as a critical alarm -- an all-clear that sounds like
    an emergency is worse than none."""
    v = render_variables(to_state={"state": "0", "label": "All-clear",
                                   "why": "nothing elevated"})
    assert v["is_critical"] is False
    assert v["push_title"] == "Creek all-clear"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    main()
