#!/usr/bin/env python3
"""
Razer HID protocol + dispatch ops for the panel.

Talks straight to /dev/hidraw* via HIDIOCSFEATURE/HIDIOCGFEATURE ioctls --
no cloud, no bridge, no credentials. Protocol and device table are vendored
from a personal tool (razer-persist) that has been live-verified on a Naga
V3 Pro and BlackWidow V3 Mini; see README.md for what that does and doesn't
guarantee on other Razer hardware.

Two capabilities:
  - battery level + charging state (read-only, safe to poll)
  - persistent (VARSTORE) static colour (write-only, on-board memory --
    never auto-apply or poll this op, see the VARSTORE warning below)

Storage flag (argument byte [0] of every lighting command):
  NOSTORE  0x00  applies to the live device, lost on power cycle
  VARSTORE 0x01  commits to the device's own flash

This always sends VARSTORE for colour writes -- the entire point of this
plugin is a colour that survives sleep/reboot with no daemon running. Flash
has a finite (if very large) write-cycle budget, so this must only ever be
invoked by an explicit user action (a panel button press), never by a timer
or on every panel open -- unlike the battery read, which is cheap and safe
to poll.

Usage (invoked by panel.qml via razer_api.js's apiCmd(), one dispatch op
per call, mirroring hue_api.py's Process-per-op pattern):
    razer_api.py get-status
    razer_api.py set-color <pid> <RRGGBB>
"""
import fcntl
import glob
import json
import os
import re
import stat
import sys
import tempfile
import time

REPORT_LEN = 90
BUF_LEN = REPORT_LEN + 1

NOSTORE = 0x00
VARSTORE = 0x01

# Transaction ids seen across Razer generations; discovery tries each in turn.
TRANSACTIONS = (0x1F, 0x9F, 0x3F, 0x08, 0x00)

# OpenRazer LED indices (razercommon.h). Not every device answers on every
# index -- status 0x05 ("not supported") for an index a device lacks is a
# correct, expected reply, not a failure. Looping over all of them rather
# than a per-device zone list is what lets this degrade gracefully on
# hardware other than the two this was built against.
LED_IDS = (0x00, 0x01, 0x04, 0x05)

# Friendly names for the two devices this has actually been tested on.
# Anything else still works (protocol/discovery below is PID-agnostic) but
# is shown as a raw "1532:PID" instead -- see README.md's hardware-scope
# disclosure.
KNOWN_DEVICES = {
    "E7": "Naga V3 Pro (wired)",
    "E8": "Naga V3 Pro (dongle)",
    "B4": "BlackWidow V3 Mini (receiver)",
    "258": "BlackWidow V3 Mini (wired)",
}

STATUS_SUCCESS = 0x02

_PID_RE = re.compile(r"[0-9A-Fa-f]{1,4}")
_COLOR_RE = re.compile(r"[0-9A-Fa-f]{6}")

MAX_STATE_BYTES = 65536


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _set_feature(n):
    return _ioc(3, "H", 0x06, n)


def _get_feature(n):
    return _ioc(3, "H", 0x07, n)


def make_report(txn, cmd_class, cmd_id, data_size, args=()):
    r = bytearray(REPORT_LEN)
    r[1] = txn
    r[5] = data_size
    r[6] = cmd_class
    r[7] = cmd_id
    for i, v in enumerate(args):
        r[8 + i] = v
    crc = 0
    for b in r[2:88]:
        crc ^= b
    r[88] = crc
    return r


def send(path, report, settle=0.06):
    # O_NONBLOCK: a sleeping wireless device's node can otherwise hang the
    # open() call rather than failing fast -- this must never block the
    # panel indefinitely just because a peripheral is asleep.
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, _set_feature(BUF_LEN), bytearray([0x00]) + report)
        time.sleep(settle)
        buf = bytearray([0x00]) + bytearray(REPORT_LEN)
        fcntl.ioctl(fd, _get_feature(BUF_LEN), buf)
        return buf[1], buf[1:]
    finally:
        os.close(fd)


def razer_nodes():
    """(path, pid) for every Razer (vendor 1532) hidraw node."""
    out = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*"),
                        key=lambda p: int(re.sub(r"\D", "", p)) if re.sub(r"\D", "", p) else 0):
        try:
            with open(os.path.join(node, "device", "uevent")) as f:
                uevent = f.read(4096)
        except OSError:
            continue
        m = re.search(r"HID_ID=[^:]*:0*1532:0*([0-9A-Fa-f]+)", uevent)
        if not m:
            continue
        pid = m.group(1).upper().lstrip("0") or "0"
        out.append(("/dev/" + os.path.basename(node), pid))
    return out


def discover():
    """(path, pid, txn) for each Razer node that answers a firmware query."""
    results = {}
    for path, pid in razer_nodes():
        if pid in results:
            continue
        for txn in TRANSACTIONS:
            try:
                status, _reply = send(path, make_report(txn, 0x00, 0x81, 0x02))
            except OSError:
                break
            if status == STATUS_SUCCESS:
                results[pid] = (path, pid, txn)
                break
    return list(results.values())


def device_name(pid):
    return KNOWN_DEVICES.get(pid, "1532:%s" % pid)


def read_battery(path, txn):
    """(percent, charging) or None if the device didn't answer cleanly."""
    try:
        status, level = send(path, make_report(txn, 0x07, 0x80, 0x02))
        _status2, charging = send(path, make_report(txn, 0x07, 0x84, 0x02))
    except OSError:
        return None
    if status != STATUS_SUCCESS:
        return None
    return (level[9] / 255 * 100, bool(charging[9]))


def apply_color(path, txn, rgb):
    """Write a persistent static colour to every LED index the device has.

    Always VARSTORE -- see the module docstring's flash-wear warning. A
    per-index "not supported" (0x05) reply is expected on devices with
    fewer zones than LED_IDS lists and is not treated as failure; only
    "every index failed" is.
    """
    r, g, b = rgb
    any_ok = False
    for led_id in LED_IDS:
        report = make_report(txn, 0x0F, 0x02, 0x09,
                              (VARSTORE, led_id, 0x01, 0x00, 0x00, 0x01, r, g, b))
        try:
            status, _reply = send(path, report)
        except OSError:
            continue
        if status in (STATUS_SUCCESS, 0x05):
            any_ok = True
    return any_ok


# ---------------------------------------------------------------------------
# Local, non-secret settings persistence (last-applied colour per device, and
# the last known-good battery reading so the panel has something to show for
# a device that's currently asleep). Same mkstemp-in-target-dir + os.replace
# atomic-write shape as every other Omarchy plugin here, even though nothing
# in this file is a secret -- a predictable settings path must still never be
# truncated through a pre-planted symlink.

def _xdg_config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


STATE_PATH = os.path.join(_xdg_config_home(), "omarchy/settings/razer-peripherals.json")


def _open_checked_dir(directory):
    os.makedirs(directory, exist_ok=True)
    dir_fd = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
    if os.fstat(dir_fd).st_uid != os.getuid():
        os.close(dir_fd)
        raise OSError("settings directory not owned by current user")
    os.chmod(dir_fd, 0o700)
    return dir_fd


def _atomic_write(path, payload):
    directory = os.path.dirname(path)
    dir_fd = _open_checked_dir(directory)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory)
        tmp_name = os.path.basename(tmp_path)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, os.path.basename(path), src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
    finally:
        os.close(dir_fd)


def _load_state():
    try:
        fd = os.open(STATE_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return {}
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return {}
        data = os.read(fd, MAX_STATE_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_STATE_BYTES:
        return {}
    try:
        obj = json.loads(data)
    except ValueError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _save_state(state):
    _atomic_write(STATE_PATH, json.dumps(state, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Dispatch ops

def _get_status():
    state = _load_state()
    known = state.get("devices", {}) if isinstance(state.get("devices"), dict) else {}
    responsive = {pid: (path, txn) for path, pid, txn in discover()}

    devices = []
    seen = set()
    for pid, (path, txn) in responsive.items():
        seen.add(pid)
        reading = read_battery(path, txn)
        entry = known.get(pid, {}) if isinstance(known.get(pid), dict) else {}
        if reading is not None:
            percent, charging = reading
            entry = {
                "name": device_name(pid),
                "percent": round(percent, 1),
                "charging": charging,
                "lastColor": entry.get("lastColor", ""),
            }
            known[pid] = entry
        devices.append({
            "pid": pid,
            "name": device_name(pid),
            "percent": entry.get("percent"),
            "charging": entry.get("charging", False),
            "lastColor": entry.get("lastColor", ""),
            "responsive": reading is not None,
        })

    # Known-but-currently-asleep devices still show their last reading, so
    # the panel doesn't blank out just because a wireless peripheral is idle.
    for pid, entry in known.items():
        if pid in seen or not isinstance(entry, dict):
            continue
        devices.append({
            "pid": pid,
            "name": entry.get("name", device_name(pid)),
            "percent": entry.get("percent"),
            "charging": entry.get("charging", False),
            "lastColor": entry.get("lastColor", ""),
            "responsive": False,
        })

    try:
        state["devices"] = known
        _save_state(state)
    except OSError:
        pass

    print(json.dumps({"devices": devices}))


def _set_color(pid, hex_color):
    if not _PID_RE.fullmatch(pid) or not _COLOR_RE.fullmatch(hex_color):
        sys.exit(1)
    pid = pid.upper().lstrip("0") or "0"
    hex_color = hex_color.upper()

    found = {p: (path, txn) for path, p, txn in discover()}
    target = found.get(pid)
    if target is None:
        sys.exit(1)
    path, txn = target
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    if not apply_color(path, txn, rgb):
        sys.exit(1)

    state = _load_state()
    devices = state.get("devices")
    if not isinstance(devices, dict):
        devices = {}
    entry = devices.get(pid) if isinstance(devices.get(pid), dict) else {}
    entry["name"] = device_name(pid)
    entry["lastColor"] = hex_color
    devices[pid] = entry
    state["devices"] = devices
    try:
        _save_state(state)
    except OSError:
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        return
    op = sys.argv[1]
    try:
        if op == "get-status":
            _get_status()
        elif op == "set-color" and len(sys.argv) >= 4:
            _set_color(sys.argv[2], sys.argv[3])
    except SystemExit:
        raise
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main() or 0)
