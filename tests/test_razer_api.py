import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import razer_api as ra


class MakeReportTests(unittest.TestCase):
    def test_length_and_header_fields(self):
        r = ra.make_report(0x1F, 0x07, 0x80, 0x02, (0x01, 0x02))
        self.assertEqual(len(r), ra.REPORT_LEN)
        self.assertEqual(r[1], 0x1F)
        self.assertEqual(r[5], 0x02)
        self.assertEqual(r[6], 0x07)
        self.assertEqual(r[7], 0x80)
        self.assertEqual(r[8], 0x01)
        self.assertEqual(r[9], 0x02)

    def test_crc_is_xor_of_2_to_88(self):
        r = ra.make_report(0x9F, 0x0F, 0x02, 0x09,
                            (ra.VARSTORE, 0x00, 0x01, 0x00, 0x00, 0x01, 0x11, 0x22, 0x33))
        crc = 0
        for b in r[2:88]:
            crc ^= b
        self.assertEqual(r[88], crc)

    def test_args_beyond_report_len_are_not_written_out_of_bounds(self):
        # args start at offset 8; only REPORT_LEN - 8 bytes of room exist.
        r = ra.make_report(0x00, 0x00, 0x00, 0x02, (1,) * (ra.REPORT_LEN - 8))
        self.assertEqual(len(r), ra.REPORT_LEN)


class RazerNodesTests(unittest.TestCase):
    def test_filters_to_vendor_1532_and_parses_pid(self):
        globbed = ["/sys/class/hidraw/hidraw3", "/sys/class/hidraw/hidraw10"]
        uevents = {
            "/sys/class/hidraw/hidraw3/device/uevent": "HID_ID=0003:00001532:0000E8\n",
            "/sys/class/hidraw/hidraw10/device/uevent": "HID_ID=0003:0000046D:0000C52B\n",
        }

        def fake_open(path, *a, **kw):
            return io.StringIO(uevents[path])

        with mock.patch("glob.glob", return_value=globbed), \
             mock.patch("builtins.open", side_effect=lambda p, *a, **kw: fake_open(p)):
            nodes = ra.razer_nodes()

        self.assertEqual(nodes, [("/dev/hidraw3", "E8")])

    def test_missing_uevent_is_skipped_not_raised(self):
        with mock.patch("glob.glob", return_value=["/sys/class/hidraw/hidraw3"]), \
             mock.patch("builtins.open", side_effect=OSError()):
            self.assertEqual(ra.razer_nodes(), [])


class DiscoverTests(unittest.TestCase):
    def test_finds_device_that_answers_success(self):
        with mock.patch.object(ra, "razer_nodes", return_value=[("/dev/hidraw3", "E8")]), \
             mock.patch.object(ra, "send", return_value=(ra.STATUS_SUCCESS, bytearray(ra.REPORT_LEN))):
            found = ra.discover()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][:2], ("/dev/hidraw3", "E8"))

    def test_gives_up_on_oserror_and_tries_next_device(self):
        with mock.patch.object(ra, "razer_nodes", return_value=[("/dev/hidraw3", "E8")]), \
             mock.patch.object(ra, "send", side_effect=OSError()):
            self.assertEqual(ra.discover(), [])

    def test_non_success_status_tries_next_transaction(self):
        calls = []

        def fake_send(path, report):
            calls.append(report[1])
            if report[1] == ra.TRANSACTIONS[-1]:
                return (ra.STATUS_SUCCESS, bytearray(ra.REPORT_LEN))
            return (0x00, bytearray(ra.REPORT_LEN))

        with mock.patch.object(ra, "razer_nodes", return_value=[("/dev/hidraw3", "E8")]), \
             mock.patch.object(ra, "send", side_effect=fake_send):
            found = ra.discover()
        self.assertEqual(len(found), 1)
        self.assertEqual(calls, list(ra.TRANSACTIONS))


class DeviceNameTests(unittest.TestCase):
    def test_known_pid_uses_friendly_name(self):
        self.assertEqual(ra.device_name("E8"), "Naga V3 Pro (dongle)")

    def test_unknown_pid_falls_back_to_raw_id(self):
        self.assertEqual(ra.device_name("1234"), "1532:1234")


class ReadBatteryTests(unittest.TestCase):
    def test_parses_percent_and_charging(self):
        def fake_send(path, report):
            if report[7] == 0x80:
                level = bytearray(ra.REPORT_LEN)
                level[9] = 191  # ~74.9%
                return (ra.STATUS_SUCCESS, level)
            charging = bytearray(ra.REPORT_LEN)
            charging[9] = 1
            return (ra.STATUS_SUCCESS, charging)

        with mock.patch.object(ra, "send", side_effect=fake_send):
            result = ra.read_battery("/dev/hidraw3", 0x1F)
        self.assertIsNotNone(result)
        percent, charging = result
        self.assertAlmostEqual(percent, 191 / 255 * 100, places=3)
        self.assertTrue(charging)

    def test_non_success_status_returns_none(self):
        with mock.patch.object(ra, "send", return_value=(0x00, bytearray(ra.REPORT_LEN))):
            self.assertIsNone(ra.read_battery("/dev/hidraw3", 0x1F))

    def test_oserror_returns_none(self):
        with mock.patch.object(ra, "send", side_effect=OSError()):
            self.assertIsNone(ra.read_battery("/dev/hidraw3", 0x1F))


class ApplyColorTests(unittest.TestCase):
    def test_sends_varstore_to_every_led_id(self):
        seen = []

        def fake_send(path, report):
            seen.append((report[8], report[9]))  # storage flag, led id
            return (ra.STATUS_SUCCESS, bytearray(ra.REPORT_LEN))

        with mock.patch.object(ra, "send", side_effect=fake_send):
            ok = ra.apply_color("/dev/hidraw3", 0x1F, (0x00, 0x22, 0xAA))
        self.assertTrue(ok)
        self.assertEqual(len(seen), len(ra.LED_IDS))
        for storage, led_id in seen:
            self.assertEqual(storage, ra.VARSTORE)
        self.assertEqual([led for _s, led in seen], list(ra.LED_IDS))

    def test_not_supported_led_does_not_fail_whole_call(self):
        with mock.patch.object(ra, "send", return_value=(0x05, bytearray(ra.REPORT_LEN))):
            ok = ra.apply_color("/dev/hidraw3", 0x1F, (0, 0, 0))
        self.assertTrue(ok)  # 0x05 counts as an acceptable per-LED reply

    def test_every_led_failing_is_reported_as_failure(self):
        with mock.patch.object(ra, "send", side_effect=OSError()):
            ok = ra.apply_color("/dev/hidraw3", 0x1F, (0, 0, 0))
        self.assertFalse(ok)


class StatePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state_path = os.path.join(self.tmpdir.name, "razer-peripherals.json")
        self._patch = mock.patch.object(ra, "STATE_PATH", self.state_path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_save_then_load_round_trips(self):
        ra._save_state({"devices": {"E8": {"lastColor": "0022AA"}}})
        loaded = ra._load_state()
        self.assertEqual(loaded["devices"]["E8"]["lastColor"], "0022AA")

    def test_load_missing_file_returns_empty_dict(self):
        self.assertEqual(ra._load_state(), {})

    def test_load_rejects_oversized_file(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w") as f:
            f.write(json.dumps({"pad": "x" * (ra.MAX_STATE_BYTES + 100)}))
        self.assertEqual(ra._load_state(), {})

    def test_load_rejects_non_dict_json(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w") as f:
            f.write(json.dumps([1, 2, 3]))
        self.assertEqual(ra._load_state(), {})

    def test_save_write_survives_pre_planted_symlink_at_destination(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        target = os.path.join(self.tmpdir.name, "attacker-owned")
        with open(target, "w") as f:
            f.write("do not touch")
        os.symlink(target, self.state_path)

        ra._save_state({"devices": {}})

        # os.replace() must not follow the destination symlink -- the real
        # settings file should now be a regular file, and the symlink's
        # original target must be untouched.
        self.assertFalse(os.path.islink(self.state_path))
        with open(target) as f:
            self.assertEqual(f.read(), "do not touch")


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.state_path = os.path.join(self.tmpdir.name, "razer-peripherals.json")
        self._patch = mock.patch.object(ra, "STATE_PATH", self.state_path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_get_status_reports_responsive_device(self):
        with mock.patch.object(ra, "discover", return_value=[("/dev/hidraw3", "E8", 0x1F)]), \
             mock.patch.object(ra, "read_battery", return_value=(50.0, False)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ra._get_status()
        payload = json.loads(out.getvalue())
        self.assertEqual(len(payload["devices"]), 1)
        self.assertEqual(payload["devices"][0]["pid"], "E8")
        self.assertEqual(payload["devices"][0]["percent"], 50.0)
        self.assertTrue(payload["devices"][0]["responsive"])

    def test_get_status_falls_back_to_last_known_for_sleeping_device(self):
        ra._save_state({"devices": {"E8": {"name": "Naga V3 Pro (dongle)",
                                             "percent": 61.2, "charging": False,
                                             "lastColor": "0022AA"}}})
        with mock.patch.object(ra, "discover", return_value=[]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ra._get_status()
        payload = json.loads(out.getvalue())
        self.assertEqual(len(payload["devices"]), 1)
        self.assertFalse(payload["devices"][0]["responsive"])
        self.assertEqual(payload["devices"][0]["percent"], 61.2)

    def test_set_color_rejects_malformed_pid_or_color(self):
        with self.assertRaises(SystemExit):
            ra._set_color("zz", "0022AA")
        with self.assertRaises(SystemExit):
            ra._set_color("E8", "not-a-color")

    def test_set_color_rejects_unresponsive_device(self):
        with mock.patch.object(ra, "discover", return_value=[]):
            with self.assertRaises(SystemExit):
                ra._set_color("E8", "0022AA")

    def test_set_color_persists_last_color_on_success(self):
        with mock.patch.object(ra, "discover", return_value=[("/dev/hidraw3", "E8", 0x1F)]), \
             mock.patch.object(ra, "apply_color", return_value=True):
            ra._set_color("E8", "0022aa")
        state = ra._load_state()
        self.assertEqual(state["devices"]["E8"]["lastColor"], "0022AA")

    def test_set_color_exits_nonzero_when_apply_fails(self):
        with mock.patch.object(ra, "discover", return_value=[("/dev/hidraw3", "E8", 0x1F)]), \
             mock.patch.object(ra, "apply_color", return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                ra._set_color("E8", "0022AA")
        self.assertNotEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
