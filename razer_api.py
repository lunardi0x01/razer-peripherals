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

# The two devices this has actually been tested on. A peripheral that can
# run both wired and wireless enumerates as two *different* PIDs -- a cable
# and a dongle/receiver are separate USB devices as far as hidraw is
# concerned -- so this table is keyed by PID but carries the model name
# they share. _merge_devices() collapses a model's PIDs back into the one
# physical peripheral the user actually owns, and "connection" is what
# tells them which way it's talking right now.
#
# "kind" is the physical slot a PID occupies, so the bar widget can show a
# keyboard reading and a mouse reading side by side rather than just
# "whichever of the N devices is lowest".
#
# Anything not listed still works (protocol/discovery below is PID-agnostic)
# but is shown as a raw "1532:PID" with no connection label, and is never
# merged with anything -- see README.md's hardware-scope disclosure.
KNOWN_DEVICES = {
    "E7": {"model": "Naga V3 Pro", "kind": "mouse", "connection": "wired"},
    "E8": {"model": "Naga V3 Pro", "kind": "mouse", "connection": "wireless"},
    "B4": {"model": "BlackWidow V3 Mini", "kind": "keyboard", "connection": "wireless"},
    "258": {"model": "BlackWidow V3 Mini", "kind": "keyboard", "connection": "wired"},
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


LOCK_TIMEOUT_SECONDS = 2


def _acquire_lock_bounded(fd, timeout=LOCK_TIMEOUT_SECONDS):
    # Non-blocking flock in a bounded retry loop, not a plain blocking
    # LOCK_EX -- a wedged sibling process holding the lock would otherwise
    # stall this call (and the panel behind it) forever, the same DoS shape
    # avoided elsewhere in this codebase's settings-file locking.
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)


def send(path, report, settle=0.06):
    # O_NONBLOCK: a sleeping wireless device's node can otherwise hang the
    # open() call rather than failing fast -- this must never block the
    # panel indefinitely just because a peripheral is asleep.
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        # Two shell instances (one per monitor) each run their own poll
        # timer against the same hidraw node with no other coordination --
        # confirmed on real hardware that concurrent, unlocked SET_FEATURE/
        # GET_FEATURE pairs interleave and corrupt each other's reply
        # (observed: a battery read racing another process's in-flight
        # request came back as a different device's reading entirely).
        # flock on the fd serializes the whole request/reply pair across
        # processes; it's released automatically when the fd closes below,
        # so there's no separate unlock path to forget.
        if not _acquire_lock_bounded(fd):
            raise OSError("timed out waiting for another process's HID request")
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
    info = KNOWN_DEVICES.get(pid)
    return info["model"] if info else "1532:%s" % pid


def device_kind(pid):
    info = KNOWN_DEVICES.get(pid)
    return info["kind"] if info else "unknown"


def device_connection(pid):
    """"wired"/"wireless", or "" for a PID that isn't in the table."""
    info = KNOWN_DEVICES.get(pid)
    return info["connection"] if info else ""


def device_group(pid):
    """Stable id for the physical peripheral a PID belongs to.

    Both of a model's PIDs map to the same id; an unrecognized PID gets an
    id of its own so it is never merged with anything.
    """
    return device_name(pid)


def group_pids(pid):
    """Every known PID belonging to the same physical device as `pid`."""
    group = device_group(pid)
    siblings = sorted(p for p, info in KNOWN_DEVICES.items() if info["model"] == group)
    return siblings or [pid]


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

def _device_record(pid, entry, responsive):
    """One PID's view of a device, before sibling PIDs are merged in.

    Everything but the battery reading comes from the local table rather
    than the settings file, so a state file written by an older version
    (which stored names like "Naga V3 Pro (dongle)") re-labels itself
    instead of keeping a name format that no longer exists.
    """
    percent = entry.get("percent")
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        percent = None
    updated = entry.get("updatedAt")
    if not isinstance(updated, (int, float)) or isinstance(updated, bool):
        updated = 0
    color = entry.get("lastColor")
    return {
        "id": device_group(pid),
        "pid": pid,
        "name": device_name(pid),
        "kind": device_kind(pid),
        "connection": device_connection(pid),
        "percent": percent,
        "charging": bool(entry.get("charging", False)),
        "lastColor": color if isinstance(color, str) else "",
        "responsive": responsive,
        "updatedAt": updated,
    }


def _activity_rank(record):
    # Which of a model's interfaces should speak for it: one that answered
    # just now beats a remembered reading, a real percentage beats a blank
    # one, and the cable beats the dongle when both are live -- plugging in
    # is the thing the user just did, so that's the state to reflect.
    return (
        1 if record["responsive"] else 0,
        1 if record["percent"] is not None else 0,
        1 if record["connection"] == "wired" else 0,
        record["updatedAt"],
    )


def _merge_devices(records):
    """Collapse a model's wired + wireless PIDs into one panel entry.

    Plugging a wireless keyboard in mid-session enumerates a second,
    different PID while the receiver is still present and still remembered
    at whatever charge it last reported -- listing both shows the same
    peripheral twice, one of them stale (the exact "receiver still says 5%
    while the cable says 100%" confusion this fixes). The live interface
    wins; a sibling's remembered colour is carried over, since VARSTORE
    colour lives in the device's own flash and is the same colour
    whichever way it's connected.

    An unrecognized PID has an id of its own (see device_group), so this
    never merges two devices it doesn't actually know to be one.
    """
    groups = {}
    order = []
    for record in records:
        if record["id"] not in groups:
            groups[record["id"]] = []
            order.append(record["id"])
        groups[record["id"]].append(record)

    merged = []
    for group_id in order:
        members = groups[group_id]
        winner = dict(max(members, key=_activity_rank))
        if not winner["lastColor"]:
            for member in members:
                if member["lastColor"]:
                    winner["lastColor"] = member["lastColor"]
                    break
        winner.pop("updatedAt", None)
        merged.append(winner)
    return merged


def _get_status():
    state = _load_state()
    known = state.get("devices", {}) if isinstance(state.get("devices"), dict) else {}
    responsive = {pid: (path, txn) for path, pid, txn in discover()}

    records = []
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
                "updatedAt": int(time.time()),
            }
            known[pid] = entry
        records.append(_device_record(pid, entry, reading is not None))

    # Known-but-currently-asleep devices still show their last reading, so
    # the panel doesn't blank out just because a wireless peripheral is idle.
    for pid, entry in known.items():
        if pid in seen or not isinstance(entry, dict):
            continue
        records.append(_device_record(pid, entry, False))

    try:
        state["devices"] = known
        _save_state(state)
    except OSError:
        pass

    print(json.dumps({"devices": _merge_devices(records)}))


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
    # The colour is committed to the device's own flash, so it's the same
    # colour whichever interface it was written through -- remember it for
    # the model's other PID too, or the panel's swatch would come up empty
    # the moment the user plugs in the cable.
    for sibling in group_pids(pid):
        entry = devices.get(sibling) if isinstance(devices.get(sibling), dict) else {}
        entry["name"] = device_name(sibling)
        entry["lastColor"] = hex_color
        devices[sibling] = entry
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
