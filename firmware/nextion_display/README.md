# Gateway v2 Nextion display: HMI contract and build checklist

The TJC3224T024_011R (2.4", 320×240, resistive touch) on gateway v2: TJC's Chinese-market
twin of the Nextion NX3224T024. Same hardware and instruction set, but the panel only accepts a
TFT compiled for a TJC model by TJC's editor, **USART HMI** (a Nextion Editor TFT is refused
with "Model does not match"). USART HMI is Chinese-only; see [Editor glossary](#editor-glossary).
The firmware side is
[`display.yaml`](../esp32s3_feather_gateway/display.yaml) plus
[`display_core.h`](../esp32s3_feather_gateway/components/creek_display/display_core.h). The
design is in the
[spec](../../docs/superpowers/specs/2026-10-10-gateway-v2-nextion-display-design.md).

This file is the **contract** between the HMI and the firmware. The firmware writes components
by **name**, so every name in the tables must match exactly. It recognises the seven
touch-reporting buttons by **page id + component id**, which must match
[Touch ids](#touch-ids) below and the `ID_*` constants in `display_core.h`. The Editor assigns
component ids in creation order (the page itself is 0), so deleting or re-creating a component
can shift them. After any structural edit, re-check the ids in the simulator. If one moved,
change the table, the `ID_*` constant and its test together. If a name or id is wrong, the
field stays blank or the button does nothing.

| File | What it is |
|---|---|
| `creek_gateway_v2_display.HMI` | USART HMI project (source) |
| `creek_gateway_v2_display.tft` | The export the display runs. The gateway downloads it from `main` on GitHub. |
| `assets/startup.png` | Splash, 320×240 |
| `assets/bg.png` | Background for every other page, 320×240 |
| `assets/ror16.zi`, `ror24.zi`, `ror48.zi` | Fonts 0 / 1 / 2 (16, 24 and 48 px) from the font generator (工具 → 字库制作). Kept so the HMI can be rebuilt. |

## Global settings

- [ ] Device (设备): **TJC3224T024_011**, display direction **landscape** (横屏), 320×240.
- [ ] `program.s`, before `page 0`:

  ```
  baud=115200
  bkcmd=0
  ```

- [ ] Pictures, in this order: **pic 0** = `assets/startup.png`, **pic 1** = `assets/bg.png`.
- [ ] Fonts (工具 → 字库制作, the font generator), encoding **iso-8859-1** (so `°` is one byte), in this
  order:
  - **font 0**: 16 px.
  - **font 1**: 24 px.
  - **font 2**: 48 px (stage).
- [ ] Every text component:
  - `sta` = crop image (**切图**, not 图片, which draws a whole picture from `pic`), and
    `picc` = 1, so the background shows through. A wrong one draws nothing at all, and the
    gateway log fills with `Invalid picture ID` and `Queue empty` (the panel's own `0x04`
    drawing error). In the simulator, `ref <name>` answers `04 FF FF FF` for a bad label.
  - `pco` = 65535 (white);
  - `txt_maxl` = **20**, except `tMsg`, which is **200**. Longer text is cut off.
- [ ] Every button: `sta` = solid color, with a `bco2` (pressed colour) different from `bco`,
  so a press visibly registers.
- [ ] Every page's **Postinitialize Event** contains `sendme`. That is how the gateway learns
  the page changed when navigation happens inside the HMI.

## Pages

| Id | Name | Background |
|---|---|---|
| 0 | `splash` | `sta` = image, `pic` = 0 |
| 1 | `main` | `sta` = image, `pic` = 1 |
| 2 | `settings` | `sta` = image, `pic` = 1 |
| 3 | `confirm` | `sta` = image, `pic` = 1 |

`splash` has no components. The Nextion shows it at power-on by itself. The gateway moves to
`main` 3 s after it connects.

### Page 1 `main`

| # | Name | Type | x, y, w, h | Font | Example |
|---|---|---|---|---|---|
| 1 | `tNode` | Text, left | 4, 2, 120, 22 | 0 | `node 42 s` |
| 2 | `tHA` | Text, center | 128, 2, 90, 22 | 0 | `HA ok` |
| 3 | `tSD` | Text, right | 222, 2, 94, 22 | 0 | `SD ok` |
| 4 | `tStage` | Text, left | 4, 30, 182, 56 | 2 | `2.41 ft` |
| 5 | `tDepth` | Text, left | 4, 88, 182, 22 | 0 | `depth 28.9 in` |
| 6 | `tTier` | Text, center | 190, 30, 126, 34 | 1 | `Watch` |
| 7 | `tRise` | Text, center | 190, 68, 126, 22 | 0 | `+0.05 in/min` |
| 8 | `tRate` | Text, left | 4, 120, 182, 30 | 1 | `0.42 in/h` |
| 9 | `tTemp` | Text, right | 230, 124, 86, 22 | 0 | `61°F` |
| 10 | `tEvent` | Text, left | 4, 154, 150, 22 | 0 | `event 1.37 in` |
| 11 | `t24h` | Text, left | 160, 154, 156, 22 | 0 | `24 h 1.52 in` |
| 12 | `tNodeBat` | Text, left | 4, 184, 112, 20 | 0 | `node 4.02 V` |
| 13 | `jNodeBat` | Progress bar | 4, 206, 112, 14 | – | `val` 0–100 |
| 14 | `tGwBat` | Text, left | 122, 184, 112, 20 | 0 | `gw 87 %` |
| 15 | `jGwBat` | Progress bar | 122, 206, 112, 14 | – | `val` 0–100 |
| 16 | `bSet` | Button | 240, 184, 76, 52 | 0 | `Settings` |

- [ ] `bSet` Touch Release Event code: `page settings`. Do not check Send Component ID.
- [ ] Progress bars: `sta` = solid color, a dark `bco`. The gateway sets `pco` (the fill).

### Page 2 `settings`

The id column is the HMI as built (TJC build, 2026-10-12: table order). Only the
touch-reporting buttons' ids (bold) matter to the firmware.

| id | Name | Type | x, y, w, h | Font | Touch Release |
|---|---|---|---|---|---|
| 1 | `tTitle` | Text | 4, 6, 200, 30 | 1 | – (text `Settings`) |
| 2 | `bBack` | Button | 240, 4, 76, 40 | 0 | code `page main` (`Back`) |
| 3 | `tBriLbl` | Text | 4, 60, 112, 24 | 0 | – (`Brightness`) |
| **4** | `bBriDn` | Button | 120, 52, 50, 40 | 1 | **Send Component ID** (`-`) |
| 5 | `tBri` | Text, center | 172, 58, 76, 28 | 0 | – (`70 %`) |
| **6** | `bBriUp` | Button | 250, 52, 50, 40 | 1 | **Send Component ID** (`+`) |
| 7 | `tSlpLbl` | Text | 4, 108, 112, 24 | 0 | – (`Sleep after`) |
| **8** | `bSlpDn` | Button | 120, 100, 50, 40 | 1 | **Send Component ID** (`-`) |
| 9 | `tSlp` | Text, center | 172, 106, 76, 28 | 0 | – (`2 min`) |
| **10** | `bSlpUp` | Button | 250, 100, 50, 40 | 1 | **Send Component ID** (`+`) |
| **11** | `bUpdate` | Button | 4, 156, 150, 50 | 0 | **Send Component ID** (`Update display`) |
| **12** | `bRestart` | Button | 166, 156, 150, 50 | 0 | **Send Component ID** (`Restart gateway`) |
| 13 | `tVer` | Text | 4, 216, 312, 20 | 0 | – (`HMI v1  2026-10-10`; bump on each export) |

### Page 3 `confirm`

| # | Name | Type | x, y, w, h | Font | Touch Release |
|---|---|---|---|---|---|
| 1 | `tMsg` | Text, multi-line (`isbr` = 1), `txt_maxl` 200 | 10, 16, 300, 140 | 0 | – |
| 2 | `bNo` | Button | 20, 172, 130, 56 | 0 | code `page settings` (`Cancel`) |
| 3 | `bYes` | Button | 170, 172, 130, 56 | 0 | **Send Component ID** (`Confirm`) |

### Colours the gateway sets (RGB565 `pco`)

| Colour | Value | Used for |
|---|---|---|
| white | 65535 | normal values |
| grey | 33808 | missing values (`--`) |
| green | 2016 | All-clear, `HA ok`, `SD ok`, healthy batteries |
| yellow | 65504 | Advisory, middling batteries |
| orange | 64512 | Watch |
| red | 63488 | Warning, `node offline`, `HA offline`, `SD fault`, low batteries |
| magenta | 63519 | Emergency |

## Check in the simulator (调试, Debug)

- [ ] Each page renders over its background; Settings, Back and Cancel navigate.
- [ ] In the return-data pane, releasing each touch-reporting button shows its bytes from
  [Touch ids](#touch-ids).

### Touch ids

As built (TJC build, 2026-10-12, created in the tables' order). These are the `ID_*`
constants in `display_core.h`, and its test pins them.

| Button | Page | id | Bytes on release |
|---|---|---|---|
| `bBriDn` | settings (2) | 4 | `65 02 04 00 FF FF FF` |
| `bBriUp` | settings (2) | 6 | `65 02 06 00 FF FF FF` |
| `bSlpDn` | settings (2) | 8 | `65 02 08 00 FF FF FF` |
| `bSlpUp` | settings (2) | 10 | `65 02 0A 00 FF FF FF` |
| `bUpdate` | settings (2) | 11 | `65 02 0B 00 FF FF FF` |
| `bRestart` | settings (2) | 12 | `65 02 0C 00 FF FF FF` |
| `bYes` | confirm (3) | 3 | `65 03 03 00 FF FF FF` |

- [ ] Entering a page also shows `66 0N FF FF FF` (the `sendme`).

## Export and ship

1. Output the TFT (the TFT output item in the 文件 / File menu) as
   `creek_gateway_v2_display.tft`, next to the HMI.
2. Commit the `.HMI`, the `.tft` and `assets/` together. CI fails if the `.HMI` was committed
   after the last `.tft` ("export the TFT").
3. Merge to `main`, because the gateway downloads the TFT from `main`'s raw URL.
4. Press **Update Display Firmware** in HA (Creek Gateway v2 > Configuration), or on the screen
   go to Settings > Update display > Confirm.
   - The radio is not serviced during the transfer, so node packets in that window are lost.
   - The gateway restarts when the transfer ends, whether it worked or not.

**First load: not over WiFi.** A new panel talks at its factory 9600 baud. The gateway talks
(and starts an upload) at 115200, so step 4 cannot reach it, and the failed upload restarts the
gateway. Load the first TFT one of these ways:
- a FAT32 microSD with only the `.tft` on it, in the display's slot, then power-cycle the
  display and remove the card when it reports success;
- or upload (下载) from USART HMI through a USB-serial adapter.

Once a TFT with `baud=115200` in `program.s` is on the panel, step 4 works for every later
update.

## Editor glossary

USART HMI has no English UI. Its layout matches the Nextion Editor, and the attribute names
(`txt`, `pco`, `sta`, `isbr`, `txt_maxl`…) are the same English identifiers. The menu words
this checklist needs:

| Chinese | Meaning |
|---|---|
| 文件 | File (打开 Open, 保存 Save, 另存为 Save As) |
| 设置 / 设备 / 型号 | Settings / Device / model |
| 横屏 / 竖屏 | Landscape / portrait |
| 编译 | Compile |
| 调试 | Debug (simulator) |
| 下载 | Upload to the device |
| 工具 → 字库制作 | Tools → font generator |
| 页面 / 图片 / 字库 | Pages / Pictures / Fonts |
| 属性 / 事件 | Attributes / Events |

## Wiring

| From | To |
|---|---|
| Feather BAT | Pololu U1V11F5 VIN |
| U1V11F5 SHDN | U1V11F5 VIN (always on) |
| U1V11F5 VOUT | Nextion 5V (red) |
| Feather GND | U1V11F5 GND, Nextion GND (black) |
| Feather TX (GPIO39) | Nextion RX (yellow) |
| Feather RX (GPIO38) | Nextion TX (blue) |

## Bring-up checklist

- [ ] The splash appears at power-on, and `main` follows about 3 s after the gateway connects.
- [ ] Every main field fills within one Ecowitt poll (60 s) and one node packet.
- [ ] Block the gateway from HA at OPNsense (as in the v2 trial).
  - Tier and rise show `--`, and `HA offline` is red.
  - Stage, rain and status keep updating.
- [ ] Node off or out of range: after the node timeout (5 min), `node offline` is red and stage
  is `--`.
- [ ] Brightness and sleep:
  - −/+ change them;
  - both survive a gateway restart;
  - the display sleeps after the timeout and wakes on touch onto `main`.
- [ ] Restart > Confirm restarts the gateway; Cancel returns to settings without acting.
- [ ] Update display, from the screen and from HA:
  - the TFT downloads, the display reboots onto the splash, and the gateway restarts;
  - record how long the transfer takes at 115200;
  - try `display_upload_baud: "921600"` and record that too.
- [ ] The log shows `touch: page 2 component 4 release` (and so on) for each button.
- [ ] The log shows no Nextion "invalid instruction" warnings over 30 min.
- [ ] Measure pack current with the display awake and asleep. On USB with the display awake,
  the pack voltage should still rise.

### Measured

| What | Value | Date |
|---|---|---|
| TFT transfer at 115200 | | |
| TFT transfer at 921600 | | |
| Pack current, display awake | | |
| Pack current, display asleep | | |
| Pack charges on USB with display awake? | | |
