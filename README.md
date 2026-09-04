# razer-peripherals

Omarchy / Quickshell bar widget showing battery levels for a Razer keyboard
and mouse, plus a persistent (on-device) lighting colour control — all
straight over USB HID, with no daemon, cloud account, or Synapse install
required.

## Features

- Bar icon showing the lowest battery percentage of any known device
- Panel listing every detected Razer device: name, battery %, charging
  state, and a colour picker
- Colour changes are written with Razer's `VARSTORE` flag, which commits
  them to the device's own flash memory — the colour survives sleep,
  reboot, and unplugging the plugin entirely, with nothing running
- A sleeping/idle wireless device still shows its last known reading
  instead of going blank

## Hardware scope — read before relying on this

This talks directly to `/dev/hidraw*` using a reverse-engineered vendor
feature-report protocol (taken from OpenRazer's `razerchromacommon.c`), not
an official Razer API — there isn't one for this. It has been **verified on
exactly two devices**:

| | Naga V3 Pro | BlackWidow V3 Mini |
|---|---|---|
| PID (wired) | `1532:00E7` | `1532:0258` |
| PID (wireless) | `1532:00E8` | arrives as `1532:00B4` |

Discovery and the colour-write path are PID-agnostic (they sweep interfaces/
transaction IDs and probe every LED index rather than assuming a fixed
layout), so other Razer HID devices may well work — but that's untested.
An unrecognized device is still shown, labelled by its raw `1532:PID`
instead of a friendly name. Community reports indicate some newer Razer
devices accept `VARSTORE` writes and silently discard them — if a colour
change doesn't stick after a sleep cycle, that's the likely reason, not a
bug in this plugin.

**No root required.** `systemd-logind` grants the logged-in session user an
ACL on these hidraw nodes via the `uaccess` udev tag, because both device
types are bound to the seat as input devices.

## Flash writes — read before mashing Apply

Every colour change is a **write to on-board flash**, not a live preview.
Flash has a large but finite write-cycle budget. This plugin never applies
a colour automatically or on a timer — only on an explicit press of
**Apply** — and you should treat it the same way: set a colour deliberately,
not as something to spam while picking a shade.

## Install

```sh
omarchy plugin add https://github.com/lunardi0x01/razer-peripherals.git --enable
```

Nothing to pair — the panel discovers connected devices live on every open,
the same way as `razer_persist.py scan`. If a wireless device shows nothing,
wake it first (press a key / move the mouse).

## Remove

```sh
~/.config/omarchy/plugins/lunardi0x01.razer-peripherals/cleanup.sh
omarchy plugin remove lunardi0x01.razer-peripherals
```

The cleanup script removes the local settings file (last-known battery
reading and last-applied colour per device — not a secret, but tidy to
remove). It does **not** undo any colour already committed to a device's
flash; that requires applying a new colour to overwrite it.

## Notes

- Settings (last known battery %, last-applied colour per device — no
  credentials, since there's nothing to authenticate) live in
  `~/.config/omarchy/settings/razer-peripherals.json`.
- The panel polls every 20 seconds while open, and not at all while closed.
  Battery reads are cheap and safe to poll; colour writes never happen on a
  timer — see the flash-write warning above.
- No brightness or lighting-effect control in this version — static colour
  only, matching the two commands this protocol has actually been
  exercised against.

## Development

See `tests/run.sh` for the test suite (`python3 -m unittest`, `node --test`,
and a bash suite for `cleanup.sh`'s `secure_remove`) — no external test
framework dependencies.
