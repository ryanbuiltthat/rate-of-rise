# Gateway v2 Store Pruning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Date: 2026-10-10
Status: Implemented 2026-10-10 (add-on 0.27.0); bench check in Task 7 pending

**Goal:** Add a Home Assistant button that tells the add-on to delete stored records older than
N days from the v2 gateway's SD card. N is an add-on option. It must work during the v2 trial
(v2 at `http://192.168.30.21`, already in `gateway_store_url`), before any cutover. v1 must
not be affected in any way.

**Architecture:** The add-on stays the only brain. The gateway gets one new endpoint,
`POST /store/prune`, which deletes whole block files. It deletes a block only when the add-on
has already consumed every record in it (its backfill cursor) **and** every record in it is
older than the cutoff. The add-on gets one option, one command and one MQTT discovery button
that uses the same `<base>/cmd/<name>` path as Retrain Now and the other existing buttons.

**Spec this amends:** [2026-10-07 gateway v2 SD backfill](../specs/2026-10-07-gateway-v2-sd-backfill-design.md).
That spec lists "SD retention or pruning" as a non-goal. This plan adds pruning as an
opt-in, button-only action. It never runs automatically.

## Why it's safe to prune, and what is lost

- A record at or below the add-on's cursor is already in HA's recorder, the long-term
  statistics, the stage log and the dataset. The SD copy is only a local archive.
- Pruning removes two things for the pruned span:
  - **Re-backfill.** Re-backfilling after `tools/backfill_undo.py`, or after the cursor is
    reset by a card swap, can only reach the records still on the card.
  - **The raw archive.** The card is the only copy of the raw per-packet records,
    including RSSI and diagnostics for unmapped fields.
- So N should be generous. The default is **90 days**. At the spec's worst case of about
  3.5 MB/day, that is roughly 315 MB, and a 32 GB card holds years. Pruning is housekeeping,
  not a capacity fix.

## How the data is laid out today (constraints for the design)

- Records are written to block files: `/node/NNNNNN.ndjson` and `/ecowitt/NNNNNN.ndjson`.
  Each block holds `BLOCK = 10000` sequence numbers (`store_core.h`).
- Each stream writes about 1,440 records a day: node at 60 s (more in fast mode), Ecowitt at
  60 s. One block therefore covers about **one week**, so pruning is block-granular and works
  in steps of about a week. This plan does not rewrite files to trim partial blocks. That
  would mean a long SD rewrite while holding the radio's bus.
- At boot, seq is recovered from the **newest** block's tail (`load_stream_`). The newest
  block is never pruned.
- `read_page_` already skips missing block files, so a store whose early blocks are gone
  still pages correctly.
- `first_seq_()` hard-codes 1 ("the store never deletes"). That stops being true and must
  change.
- The add-on never reads `streams.*.first`. The reconciler only uses `last`, `sd_ok` and
  `store_id`, so a rising `first` changes nothing in backfill.

## v1 and trial isolation

- **Firmware.** All firmware changes are in `components/creek_store/`, which only v2
  compiles. `rfm69_gateway` and everything under `firmware/esp32_rfm69_gateway/` are
  untouched. The existing CI step that compiles v1 and checks `USE_RFM69_PACKET_HOOK` is
  absent still guards this.
- **Add-on.** Pruning reuses `gateway_store_url` and `gateway_store_token`. It goes through
  the same `StoreClient.probe()` feature detection as backfill:
  - Pointed at v1, or at anything that isn't a `store_schema: 1` document, it refuses with
    "not a v2 gateway store; nothing pruned" and sends no `POST`.
  - With a blank URL, the button reports that backfill is off.
- **Trial.** Nothing depends on cutover. The trial wrapper (`creek-gateway-v2.yaml`) and the
  production wrapper both include `creek_store`, so both get the endpoint. Pruning touches
  only the card, never HA entities, so the trial entity maps and shadow maps don't matter.

## Global constraints

- The endpoint is `POST` only. Any other method returns 405. A `GET` must never delete.
- It needs a bearer token (the existing `handleRequest` check runs first).
- Never delete:
  - the newest block of a stream
  - a block containing any seq above the add-on's cursor for that stream
  - a block whose last complete record has `ts >= before`
- Deletion is **oldest-first and contiguous**. The first block that fails the rule stops
  that stream's pass, so the store never has holes.
- At most `PRUNE_MAX_BLOCKS = 8` deletions per request, which bounds how long the bus is
  held. The response says `"more": true` and the add-on repeats the request.
- The request carries the card's `store_id`. The gateway answers 409 if it doesn't match the
  mounted card. This covers a card swapped between the add-on's probe and the prune.
- The add-on only sends a prune when the probed `store_id` equals the reconciler's saved
  `store_id`. A cursor for a different card means nothing.
- Add-on option: `gateway_store_prune_days`, schema `int(7,3650)?`, default `90`.
  - 7 is the floor so a prune can never run ahead of a week's backfill.
  - Blank or unset means the button refuses with "pruning not configured".
- Add-on version becomes `0.27.0`, with a `## 0.27.0` CHANGELOG section.

---

## Task 1: Pure prune logic in `store_core.h`, host-tested

**Files:** `firmware/esp32s3_feather_gateway/components/creek_store/store_core.h`,
`firmware/esp32s3_feather_gateway/tests/test_store_core.cpp`

- [ ] Add `std::optional<double> parse_ts(const char *line)`. It reads the `"ts":` value from
  an encoded record, matching `parse_seq`'s style (no JSON library).
- [ ] Add `struct TailInfo { uint32_t seq; double ts; }` and
  `std::optional<TailInfo> last_record_in_tail(const std::string &tail)`. It returns the last
  **complete** line that has both a seq and a ts, skipping a torn final line the same way
  `last_seq_in_tail` does.
- [ ] Add `bool block_prunable(uint32_t block, uint32_t newest_block, std::optional<TailInfo> last, uint32_t through, double before)`.
  It returns false when:
  - `block >= newest_block`
  - `last` is empty, so an unreadable or empty block is kept
  - `last->seq > through`
  - `last->ts >= before`
- [ ] Add `uint32_t first_seq_from(int64_t oldest_block, uint32_t written)`. It returns 0 if
  nothing has been written, 1 if `oldest_block <= 0`, and otherwise `oldest_block * BLOCK`.
- [ ] Host tests:
  - torn tail
  - a tail with no `ts`
  - each refusal case above
  - the boundary `last.seq == through` (prunable)
  - the boundary `last.ts == before` (kept)
  - `first_seq_from` for block 0, block 3, and nothing written
- [ ] Run the CI command locally:
  `g++ -std=c++17 -Wall -Wextra -Werror -I .../creek_store .../tests/test_store_core.cpp && ./a.out`

## Task 2: Track the oldest block and report a real `first`

**Files:** `creek_store.h`

- [ ] Extend `StreamState` with `int64_t oldest{-1}`. In `load_stream_`'s directory scan,
  take the minimum valid block name as well as the maximum.
- [ ] Add `std::atomic<int64_t> oldest_block_[STREAM_COUNT]`, set in `mount_sd_` next to
  `newest_block_`. In `append_`, set it to `block` if it is `-1`; that is the first write to
  a blank card.
- [ ] Replace `first_seq_()` with `creek_core::first_seq_from(oldest_block_[s], written_seq_[s])`.
  Update the comment that says the store never deletes.
- [ ] Optimisation, optional: start `read_page_`'s loop at
  `max(block_of(after + 1), oldest_block_)`. Missing files are already skipped, so this only
  saves `SD.open` misses.

## Task 3: `POST /store/prune` on the gateway

**Files:** `creek_store.h`

- [ ] **Spike first.** Confirm how `web_server_idf`'s `AsyncWebServerRequest` exposes the
  method (for example `request->method() == HTTP_POST`) on ESPHome 2026.7.3. Also confirm
  that `getParam` reads query parameters on a POST. If it doesn't, send every argument in
  the query string, which this plan assumes anyway.
- [ ] In `handleRequest`, after the auth check, route `/store/prune` before
  `/store/records`:
  1. Return 405 unless the method is POST.
  2. Parse the parameters: `before` (epoch seconds, double), `node_through`,
     `ecowitt_through` (uint32) and `store_id`. A missing or zero `before` returns 400.
  3. Take the bus with `HTTP_BUS_WAIT`. On timeout return 503 `bus busy`.
  4. If `!sd_ok_`, return 503 `sd unavailable`. If `store_id` doesn't equal `store_id_`
     (read under `id_mutex_`), return 409 `store id mismatch`.
  5. For each stream, starting at `b = oldest_block_[s]` and while `n < PRUNE_MAX_BLOCKS`:
     - Open the block and read its last `TAIL_BYTES`, as `load_stream_` does.
     - Call `last_record_in_tail`.
     - If `!block_prunable(...)`, stop this stream.
     - Otherwise call `SD.remove(path)`. If the remove fails, stop this stream, log a
       WARNING and keep `oldest_block_`.
     - Otherwise set `oldest_block_[s] = b + 1` and increment `n` and `deleted[s]`.
  6. Release the bus.
  7. Set `last_free_ms_ = 0` so the next `publish_health_` refreshes Store Free Space.
  8. Return 200 `application/json`:
     `{"deleted":{"node":2,"ecowitt":2},"first":{"node":20000,"ecowitt":20000},"more":false}`.
     `more` is true when the loop stopped at `PRUNE_MAX_BLOCKS` and the next block would
     also qualify. Re-checking that block is cheap: it is one more tail read.
- [ ] Log one INFO line per request that deletes anything. Use the format "pruned N node /
  M ecowitt block(s); store now starts at seq X / Y".
- [ ] Threading note: the request runs on the httpd task holding the bus mutex. The main
  task's `append_` uses a non-blocking take and simply waits for the next loop. Records keep
  queuing (`QUEUE_MAX = 64`, about an hour at 60 s), and a prune of 8 blocks takes well
  under a second. Removing a block that is not the newest never races `append_`, which only
  opens the newest block.
- [ ] `esphome compile firmware/esp32s3_feather_gateway/gateway.yaml` passes. The v1
  compile and hook-absent check in CI still pass.

## Task 4: `StoreClient.prune()`

**Files:** `rate_of_rise/app/backfill/client.py`, `rate_of_rise/tests/test_backfill_prune.py` (client tests live with the prune tests, sharing their fake gateway)

- [ ] Add `prune(before: float, through: dict[str, int], store_id: str) -> dict`. It sends
  a POST with the query parameters above and the same headers and timeout as `records()`.
  It returns the parsed JSON.
  - 404 or 405 raise `PruneUnsupported`, meaning the v2 firmware predates the endpoint.
  - 409 raises `StoreChanged`.
  - Anything else that isn't a 2xx raises via `raise_for_status()`.
- [ ] Tests against the fake `http.server` gateway:
  - success with `more` true and then false
  - 401
  - 404 (old firmware)
  - 409
  - the request is a POST and carries the bearer header

## Task 5: Prune command in the add-on

**Files:** `rate_of_rise/app/backfill/prune.py` (new), `app/backfill/reconciler.py`,
`app/commands.py`, `app/__main__.py`, `app/config.py`, `config.yaml`,
`rate_of_rise/tests/test_backfill_prune.py` (new), `tests/test_commands.py`

- [ ] `config.yaml`: add `gateway_store_prune_days: 90` under options with a comment block in
  the existing style ("deletes gateway SD records older than this many days, but only ones
  backfill has already written to HA; the Prune Gateway Store button runs it"). Add the
  schema `gateway_store_prune_days: int(7,3650)?`. `config.py` reads it as `int | None`.
- [ ] `Reconciler`: expose a read-only `store_id` property (the saved id).
- [ ] `prune.py`: `prune_store(client, reconciler, days, now_fn=time.time) -> str`. Steps:
  1. If `days` is falsy, return "pruning not configured (gateway_store_prune_days)".
  2. Call `client.probe()`. If the state isn't `OK`, return
     `"not pruned: gateway store {state}"`. For a v1 gateway this reads "not a v2 gateway
     store", the same wording as the status entity.
  3. If `sd_ok` is false, return "not pruned: gateway SD not mounted".
  4. If the probed `store_id` doesn't equal `reconciler.store_id`, return "not pruned:
     backfill has not read this card yet". This covers `None` too, meaning backfill has
     never run.
  5. Set `through = reconciler.cursor`. If every stream is 0, return "nothing backfilled
     yet; nothing to prune".
  6. Set `before = now - days * 86400`. Call `client.prune(...)` in a loop while `more` is
     true, with a hard cap of 50 rounds. Add up `deleted`.
  7. If `PruneUnsupported`, return "gateway firmware has no prune endpoint; update v2". If
     `StoreChanged`, return "not pruned: gateway card changed". On a `RequestException`,
     return "not pruned: gateway unreachable".
  8. Return, for example, "pruned 3 node / 3 ecowitt block(s) older than 90 d; store now
     starts at node seq 30000, ecowitt seq 30000".
- [ ] `commands.py`: add `"prune_store"` to `KNOWN_COMMANDS`.
- [ ] `__main__.py`: add the handler
  `"prune_store": lambda payload: _prune_store(cfg, backfill)`.
  - If `backfill` is `None`, return "gateway store backfill is off (gateway_store_url blank);
    nothing to prune".
  - Otherwise call `prune_store(svc.client, svc.reconciler, cfg.gateway_store_prune_days)`.
    `BackfillService` exposes `client` and `reconciler` read-only.
  - The command runs on the main loop, like Retrain Now and with the same 10 s per-request
    timeout. Reading `reconciler.cursor` from this thread is safe: it returns a copy, and
    the cursor only ever rises, so a stale value only prunes less.
  - The outcome is published on the existing command-result topic like every other command.
    Log it at INFO, since a press is deliberate.
- [ ] Tests (`test_backfill_prune.py`, using the fake gateway and a fake reconciler):
  - **v1**: probe gets a 404 page. It refuses, sends no POST, and logs nothing above INFO.
  - blank days
  - backfill off (`backfill is None`)
  - `sd_ok` false
  - `store_id` mismatch, and `store_id` `None`
  - cursor all zero
  - multi-round `more`
  - old firmware 404 on `/store/prune`
  - 409
  - unreachable
  - the `before` value sent is `now - days*86400`, and `node_through`/`ecowitt_through`
    equal the cursor
- [ ] `test_commands.py`: `prune_store` is a known command, and the processor routes it.

## Task 6: The button

**Files:** `rate_of_rise/app/discovery.py`, `rate_of_rise/tests/test_discovery*.py`

- [ ] Add it next to the other command buttons:
  ```python
  ("button", "creek_prune_gateway_store", {
      "name": "Creek Prune Gateway Store",
      "command_topic": f"{b}/cmd/prune_store", "payload_press": "run",
      "icon": "mdi:sd", "entity_category": "config"}),
  ```
  The entity is `button.rate_of_rise_creek_prune_gateway_store`, following
  `DiscoveryPublisher.entity_ids`. `entity_category: config` keeps it off auto-generated
  dashboards, so a stray tap is less likely.
- [ ] Publish it always, like the other buttons. With backfill off it answers with a clear
  message instead of disappearing, so the discovery code doesn't become config-dependent. If
  review prefers it hidden, gate it on `cfg.gateway_store_url`, and check how discovery
  clears a retained config before doing so.
- [ ] Discovery tests: the slug appears, the entity id matches, and the command topic is
  `creek/cmd/prune_store`.

For a dashboard-only button instead, for example one that asks for confirmation, the
equivalent HA template button publishes to the same topic:

```yaml
template:
  - button:
      - name: Prune Gateway Store
        icon: mdi:sd
        press:
          - action: mqtt.publish
            data:
              topic: creek/cmd/prune_store
              payload: run
```

## Task 7: Docs, version, bench check

- [ ] `docs/gateway-v2-trial.md`: add a "Pruning the card" section covering:
  - what the button deletes: whole ~1-week blocks, only below the backfill cursor and older
    than `gateway_store_prune_days`, never the newest block
  - what is lost: re-backfill and the raw archive for that span
  - each result message and what it means
- [ ] Amend the 2026-10-07 spec: in its Non-goals, change "SD retention or pruning" to
  "automatic pruning", and link this plan.
- [ ] `firmware/README.md`: one line on `POST /store/prune` in the store API list.
- [ ] Bump `rate_of_rise/config.yaml` to `0.27.0` and add a CHANGELOG entry. CI enforces the
  pairing.
- [ ] **Bench check (v2 only, during the trial):**
  1. With a card holding at least 3 blocks per stream, set
     `gateway_store_prune_days: 7` and press the button. The oldest blocks go, the newest
     stays, `/store/status` `first` rises, and Store Free Space goes up within 60 s.
  2. Press again immediately: "pruned 0 / 0".
  3. Power-cycle v2: seq continues from the newest block, and `first` is still correct.
  4. Start a node OTA push from v1 and press during it: either a clean 503 `bus busy` ("not
     pruned: gateway unreachable") or a successful prune after the push. The OTA must not
     fail either way.
  5. Point `gateway_store_url` at v1's IP and press: "not a v2 gateway store; nothing
     pruned". v1's logs show no request to `/store/prune`.

## Out of scope

- Scheduled or automatic pruning. An HA automation that presses the button nightly can be
  added later without code changes.
- Trimming partial blocks.
- Pruning by free space instead of age.
