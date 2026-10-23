"""Relay checks with a fake bridge and a fake API: no network, no GPIO."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import relay


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeBridge:
    def __init__(self, refuse=None, down=False, frame=b"\xff\xd8\xff" + b"0" * 50):
        self.actions, self.refuse, self.down, self.jpeg = [], refuse or {}, down, frame
        self.state = {"connected": True, "state": "idle", "busy": False}

    def _check(self):
        if self.down:
            raise relay.Unavailable("connection refused")

    def status(self):
        self._check()
        return dict(self.state)

    def action(self, body):
        self._check()
        if body["action"] in self.refuse:
            raise relay.Refused(self.refuse[body["action"]], 400)
        self.actions.append(body)
        return dict(self.state)

    def frame(self):
        self._check()
        return self.jpeg


class FakeApi:
    def __init__(self, fresh=0.0, viewer=False, down=False):
        self.results, self.states, self.frames = [], [], []
        self.control = {"operator_fresh_for": fresh, "viewer_active": viewer}
        self.down = down
        self.queue = []

    def _check(self):
        if self.down:
            raise relay.Unavailable("no route to host")

    def commands(self, wait):
        self._check()
        queue, self.queue = self.queue, []
        return {"commands": queue, **self.control}

    def result(self, command_id, result):
        self._check()
        self.results.append((command_id, result))
        return dict(self.control)

    def state(self, state, remote_allowed):
        self._check()
        self.states.append((state, remote_allowed))
        return dict(self.control)

    def frame(self, jpeg):
        self._check()
        self.frames.append(jpeg)
        return {"ok": True}


def make(allow_remote=True, **api):
    clock, bridge, fake_api = Clock(), FakeBridge(), FakeApi(**api)
    return relay.Relay(fake_api, bridge, allow_remote, clock=clock), clock, bridge, fake_api


class PureRuleTests(unittest.TestCase):
    def test_deadline_is_counted_from_when_the_poll_was_sent(self):
        self.assertEqual(relay.command_deadline(4.5, 10.0), 14.5)
        self.assertIsNone(relay.command_deadline(None, 10.0))
        self.assertEqual(relay.command_deadline(-3, 10.0), 10.0)
        self.assertEqual(relay.command_deadline("x", 10.0), 10.0)
        self.assertEqual(relay.command_deadline(float("nan"), 10.0), 10.0)

    def test_expired_commands_never_run_but_stop_always_does(self):
        self.assertTrue(relay.is_runnable("test-motor", 15.0, 14.9))
        self.assertFalse(relay.is_runnable("test-motor", 15.0, 15.0))
        self.assertFalse(relay.is_runnable("scan", None, 0))  # movement needs a deadline
        self.assertTrue(relay.is_runnable("stop", None, 1e9))
        self.assertTrue(relay.is_runnable("disconnect", 1.0, 1e9))

    def test_stop_first_and_older_movement_superseded(self):
        run, superseded = relay.order_commands([
            {"id": 3, "action": "test-motor"}, {"id": 4, "action": "stop"},
            {"id": 5, "action": "connect"}, {"id": 1, "action": "scan"}])
        self.assertEqual([c["id"] for c in run], [4, 5])
        self.assertEqual([c["id"] for c in superseded], [1, 3])

    def test_remote_opt_in(self):
        self.assertEqual(relay.refusal("test-motor", False), relay.REMOTE_OFF)
        self.assertEqual(relay.refusal("acknowledge", False), relay.REMOTE_OFF)
        self.assertIsNone(relay.refusal("stop", False))
        self.assertIsNone(relay.refusal("disconnect", False))
        self.assertIsNone(relay.refusal("scan", True))
        self.assertTrue(relay.refusal("cycle", True))  # bridge-only command, not remote
        self.assertTrue(relay.refusal("heartbeat", True))  # never accepted as a command

    def test_freshness_is_capped_and_never_invented(self):
        self.assertEqual(relay.fresh_until(10.0, 2.5), 12.5)
        self.assertEqual(relay.fresh_until(10.0, 60), 10.0 + relay.MAX_FRESH_SECONDS)
        for value in (0, -1, None, "x", float("nan")):
            self.assertEqual(relay.fresh_until(10.0, value), float("-inf"))
        self.assertTrue(relay.heartbeat_due(11.0, 12.5, 9.0, True))
        self.assertFalse(relay.heartbeat_due(11.0, 12.5, 10.5, True))  # at most 1/s
        self.assertFalse(relay.heartbeat_due(13.0, 12.5, 9.0, True))  # stale
        self.assertFalse(relay.heartbeat_due(11.0, 12.5, 9.0, False))  # remote off

    def test_report_cadence(self):
        self.assertEqual(relay.state_interval(False, False, False), relay.STATE_IDLE_EVERY)
        self.assertEqual(relay.state_interval(True, False, False), relay.STATE_ACTIVE_EVERY)
        self.assertTrue(relay.frame_due(10.0, 8.9, True))
        self.assertFalse(relay.frame_due(10.0, 9.5, True))
        self.assertFalse(relay.frame_due(10.0, 0.0, False))
        self.assertTrue(relay.bridge_busy({"state": "cycle"}))
        self.assertTrue(relay.bridge_busy({"state": "idle", "workflow": {"active": True}}))
        self.assertFalse(relay.bridge_busy({"state": "idle", "busy": False}))

    def test_urls(self):
        self.assertEqual(relay.check_server_url("https://adgvs-api.onrender.com/"),
                         "https://adgvs-api.onrender.com")
        relay.check_server_url("http://127.0.0.1:8000")
        for bad in ("http://adgvs-api.onrender.com", "https://u:p@host", "https://host/api",
                    "ftp://host"):
            with self.assertRaises(ValueError):
                relay.check_server_url(bad)
        relay.check_bridge_url("http://127.0.0.1:8765")
        for bad in ("http://192.168.1.5:8765", "https://127.0.0.1:8765"):
            with self.assertRaises(ValueError):
                relay.check_bridge_url(bad)


class RelayTests(unittest.TestCase):
    def test_runs_live_commands_through_the_bridge_and_reports(self):
        r, clock, bridge, api = make()
        api.queue = [{"id": 7, "action": "test-servo3", "params": {"angle": 120},
                      "expires_in": 4.0}]
        r.poll_once()
        self.assertEqual(bridge.actions, [{"angle": 120, "action": "test-servo3"}])
        self.assertEqual(api.results[0][0], 7)
        self.assertTrue(api.results[0][1]["ok"])
        self.assertIn("status", api.results[0][1])

    def test_params_cannot_change_the_action(self):
        r, clock, bridge, api = make()
        api.queue = [{"id": 1, "action": "connect", "params": {"action": "test-motor"},
                      "expires_in": 4}]
        r.poll_once()
        self.assertEqual(bridge.actions, [{"action": "connect"}])

    def test_expired_command_is_not_run(self):
        r, clock, bridge, api = make()
        api.queue = [{"id": 1, "action": "test-motor", "params": {}, "expires_in": 5}]
        original = api.commands

        def slow_poll(wait):  # the answer arrives after the command's life ran out
            clock.now += 6
            return original(wait)
        api.commands = slow_poll
        r.poll_once()
        self.assertEqual(bridge.actions, [])
        self.assertEqual(api.results, [(1, {"ok": False, "error": "expired"})])

    def test_stop_runs_first_and_superseded_movement_is_reported(self):
        r, clock, bridge, api = make()
        api.queue = [{"id": 1, "action": "test-motor", "params": {}, "expires_in": 5},
                     {"id": 2, "action": "stop", "params": {}, "expires_in": None},
                     {"id": 3, "action": "connect", "params": {}, "expires_in": 5}]
        r.poll_once()
        self.assertEqual([a["action"] for a in bridge.actions], ["stop", "connect"])
        self.assertIn((1, {"ok": False, "error": "superseded"}), api.results)

    def test_without_opt_in_only_stop_and_disconnect_run(self):
        r, clock, bridge, api = make(allow_remote=False, fresh=3.0)
        api.queue = [{"id": 1, "action": "disconnect", "params": {}, "expires_in": None},
                     {"id": 2, "action": "scan", "params": {"session_id": 4},
                      "expires_in": 5}]
        r.poll_once()
        self.assertEqual(bridge.actions, [{"action": "disconnect"}])
        self.assertIn((2, {"ok": False, "error": relay.REMOTE_OFF}), api.results)
        self.assertFalse(r.heartbeat_tick())  # no heartbeats either
        r.report_tick()
        self.assertEqual(api.states[0][1], False)  # tells the API remote control is off

    def test_bridge_refusal_and_outage_are_reported(self):
        r, clock, bridge, api = make()
        bridge.refuse = {"test-motor": "Connect the hardware first."}
        api.queue = [{"id": 1, "action": "test-motor", "params": {}, "expires_in": 5}]
        r.poll_once()
        self.assertEqual(api.results[-1], (1, {"ok": False,
                                               "error": "Connect the hardware first."}))
        bridge.down = True
        api.queue = [{"id": 2, "action": "connect", "params": {}, "expires_in": 5}]
        r.poll_once()
        self.assertEqual(api.results[-1], (2, {"ok": False, "error": relay.BRIDGE_DOWN}))
        clock.now += 10
        r.report_tick()
        self.assertEqual(api.states[-1][0]["error"], relay.BRIDGE_DOWN)

    def test_heartbeats_only_while_the_operator_is_fresh(self):
        r, clock, bridge, api = make(fresh=2.0)
        r.poll_once()  # learns: operator fresh for 2 s from the poll's send time
        self.assertTrue(r.heartbeat_tick())
        self.assertEqual(bridge.actions, [{"action": "heartbeat"}])
        clock.now += 0.5
        self.assertFalse(r.heartbeat_tick())  # at most one per second
        clock.now += 1.0
        self.assertTrue(r.heartbeat_tick())
        clock.now += 1.0  # 2.5 s after the answer: stale, and no new answer came
        self.assertFalse(r.heartbeat_tick())
        self.assertEqual(len(bridge.actions), 2)

    def test_api_outage_stops_heartbeats_so_the_bridge_stops_itself(self):
        r, clock, bridge, api = make(fresh=3.0)
        r.report_tick()
        self.assertTrue(r.heartbeat_tick())
        api.down = True
        beats = 0
        for _ in range(10):
            clock.now += 1.0
            r.report_tick()  # state reports fail: no new freshness
            with self.assertRaises(relay.Unavailable):
                r.poll_once()
            beats += r.heartbeat_tick()
        self.assertLessEqual(beats, 2)  # only within the last fresh window, then none
        self.assertFalse(r.heartbeat_tick())

    def test_operator_going_stale_stops_heartbeats(self):
        r, clock, bridge, api = make(fresh=3.0)
        r.report_tick()
        api.control["operator_fresh_for"] = 0.0
        clock.now += 1.0
        r.busy = True  # active: state every second
        r.report_tick()
        self.assertFalse(r.heartbeat_tick())

    def test_older_answers_do_not_override_newer_control_information(self):
        r, clock, bridge, api = make()
        r.control({"operator_fresh_for": 0.0, "viewer_active": False}, 105.0)
        r.control({"operator_fresh_for": 3.0, "viewer_active": True}, 101.0)  # long-poll
        clock.now = 105.5
        self.assertFalse(r.operator_fresh(clock.now))
        self.assertFalse(r.viewer_active)

    def test_frames_only_while_a_viewer_is_active_at_most_one_per_second(self):
        r, clock, bridge, api = make()
        self.assertEqual(r.report_tick(), ["state"])
        self.assertEqual(api.frames, [])
        api.control["viewer_active"] = True
        clock.now += relay.STATE_IDLE_EVERY
        self.assertEqual(r.report_tick(), ["state", "frame"])
        clock.now += 0.5
        self.assertEqual(r.report_tick(), [])
        clock.now += 0.6
        self.assertEqual(r.report_tick(), ["state", "frame"])
        bridge.jpeg = b"\xff\xd8\xff" + b"0" * relay.MAX_FRAME_BYTES  # too large: skipped
        clock.now += 1.1
        self.assertEqual(r.report_tick(), ["state"])

    def test_state_cadence_follows_activity(self):
        r, clock, bridge, api = make()
        r.report_tick()
        clock.now += 1.5
        self.assertEqual(r.report_tick(), [])  # idle: every 5 s
        bridge.state = {"connected": True, "state": "cycle", "busy": True}
        clock.now += 4
        r.report_tick()
        clock.now += 1.1
        self.assertEqual(r.report_tick(), ["state"])  # busy: every second


if __name__ == "__main__":
    unittest.main()
