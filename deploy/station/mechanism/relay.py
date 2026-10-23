"""Outbound relay: operators on the cloud site control this Pi's camera mechanism.

The API (Render) never connects to the Pi. This process connects out to it with the station
key, long-polls the commands operators sent (GET /api/station/mechanism/commands), runs each
one through the local bridge's keyed integration endpoints (server.py on 127.0.0.1:8765) and
posts the result back. It also reports the bridge's state (about every second while
something is happening, every 5 s otherwise) and, only while someone watches the preview,
camera frames (at most one per second). docs/cloud-motor-control.md

Safety (the pure functions below are unit-tested in tests/test_relay.py):

- Local opt-in: without --allow-remote (or ADGVS_MECHANISM_REMOTE=1) the relay only reports
  state and runs stop / disconnect; every movement command is refused.
- Command expiry: the API gives each command its remaining life in seconds. The relay counts
  it from when it SENT the poll (on its own clock) and never runs an expired command. stop and
  disconnect never expire and run first; movement queued before a stop is dropped.
- Dead-man: the bridge stops its outputs when it gets no heartbeat for 4 s. The relay forwards
  a heartbeat only while the API says the controlling operator's own heartbeat is fresh
  (operator_fresh_for). It never invents one: if the operator's browser or the connection to
  the API goes away, the heartbeats stop and the bridge stops by itself.
- The bridge's own checks (cycle active, paper held, angles) stay in server.py, unchanged.

Run (deploy/station/run-cloud-mechanism.sh starts the bridge and this together):
    ADGVS_STATION_KEY=... ADGVS_MECHANISM_KEY=... \\
        python relay.py --server https://adgvs-api.onrender.com --allow-remote
"""

import argparse
import json
import logging
import os
import signal
import threading
import urllib.error
import urllib.request
from time import monotonic
from urllib.parse import urlencode, urlsplit

log = logging.getLogger("mechanism.relay")

SAFE_ACTIONS = frozenset({"stop", "disconnect"})
BRIDGE_ACTIONS = frozenset({"scan", "stop", "acknowledge", "connect", "disconnect", "up",
                            "test-motor", "test-servo1", "test-servo2", "test-servo3"})
MAX_FRESH_SECONDS = 3.0  # never trust a longer operator freshness than the API's window
HEARTBEAT_EVERY = 1.0  # the bridge's dead-man fires after 4 s
STATE_ACTIVE_EVERY = 1.0
STATE_IDLE_EVERY = 5.0
FRAME_EVERY = 1.0
MAX_FRAME_BYTES = 200_000
POLL_WAIT = 20
RETRY_SECONDS = 2.0
REFUSED_RETRY_SECONDS = 30.0  # wrong key, not the camera station, relay off on the server
REMOTE_OFF = "Remote control is switched off on this station."
BRIDGE_DOWN = "The local mechanism service is not running on the station."


# ---- pure rules ------------------------------------------------------------------------------

def command_deadline(expires_in, sent_at):
    """Local monotonic deadline of a command, counted from when the poll was sent."""
    if expires_in is None:
        return None
    try:
        seconds = float(expires_in)
    except (TypeError, ValueError):
        return sent_at  # malformed: already expired
    if seconds != seconds:  # NaN
        return sent_at
    return sent_at + max(0.0, seconds)


def is_runnable(action, deadline, now):
    """stop / disconnect always run; anything else only before its deadline. A movement
    command without a deadline is treated as expired (the API always sends one)."""
    if action in SAFE_ACTIONS:
        return True
    return deadline is not None and now < deadline


def order_commands(commands):
    """(run in this order, superseded): stop/disconnect first; movement queued before the
    newest stop/disconnect in the batch is dropped (the operator's last word was stop)."""
    def cid(command):
        value = command.get("id")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    last_stop = max((cid(c) for c in commands if c.get("action") in SAFE_ACTIONS), default=0)
    run, superseded = [], []
    for command in sorted(commands, key=cid):
        if command.get("action") not in SAFE_ACTIONS and cid(command) < last_stop:
            superseded.append(command)
        else:
            run.append(command)
    run.sort(key=lambda c: (c.get("action") not in SAFE_ACTIONS, cid(c)))
    return run, superseded


def refusal(action, allow_remote):
    """Why this station will not run the action, or None."""
    if action not in BRIDGE_ACTIONS:
        return "Unknown mechanism action."
    if not allow_remote and action not in SAFE_ACTIONS:
        return REMOTE_OFF
    return None


def fresh_until(sent_at, operator_fresh_for):
    """Until when (local monotonic) heartbeats may be forwarded, from one API answer."""
    try:
        seconds = float(operator_fresh_for)
    except (TypeError, ValueError):
        return float("-inf")
    if not seconds > 0:  # also NaN
        return float("-inf")
    return sent_at + min(seconds, MAX_FRESH_SECONDS)


def heartbeat_due(now, until, last_heartbeat, allow_remote):
    return allow_remote and now < until and now - last_heartbeat >= HEARTBEAT_EVERY


def state_interval(busy, operator_fresh, viewer_active):
    return STATE_ACTIVE_EVERY if (busy or operator_fresh or viewer_active) else STATE_IDLE_EVERY


def frame_due(now, last_frame, viewer_active):
    return bool(viewer_active) and now - last_frame >= FRAME_EVERY


def bridge_busy(status):
    workflow = status.get("workflow") or {}
    return bool(status.get("busy") or workflow.get("active")
                or status.get("state") not in (None, "idle", "disconnected"))


def offline_state():
    return {"connected": False, "state": "disconnected", "busy": False,
            "label": "Mechanism service offline", "error": BRIDGE_DOWN}


# ---- HTTP clients (standard library only) ----------------------------------------------------

class Unavailable(Exception):
    """The other side could not be reached or answered unexpectedly."""


class Refused(Exception):
    """The other side answered with a reason (bridge 400/503, API 4xx)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is an error here, never followed


def _opener():
    # Like the bridge: no proxies from the environment, no redirects.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _message(error):
    try:
        body = json.loads(error.read(4096) or b"{}")
    except ValueError:
        body = {}
    text = body.get("error") or body.get("detail") if isinstance(body, dict) else None
    return text if isinstance(text, str) else f"HTTP {error.code}"


def check_bridge_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost")
            or parts.username or parts.password or parts.path not in ("", "/")
            or parts.query or parts.fragment):
        raise ValueError("The bridge URL must be a loopback http:// origin")
    return url.rstrip("/")


def check_server_url(url):
    parts = urlsplit(url)
    local = parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
    if (not (parts.scheme == "https" or local) or not parts.hostname
            or parts.username or parts.password or parts.path not in ("", "/")
            or parts.query or parts.fragment):
        raise ValueError("--server must be the API origin, e.g. https://adgvs-api.onrender.com")
    return url.rstrip("/")


class HttpBridge:
    def __init__(self, url, key, timeout=5):
        self.url, self.key, self.timeout = check_bridge_url(url), key, timeout
        self.opener = _opener()

    def _call(self, method, path, body=None):
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        headers = {"X-ADGVS-Bridge-Key": self.key}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=data, headers=headers,
                                         method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.headers.get("Content-Type", ""), response.read(MAX_FRAME_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code in (400, 503):
                raise Refused(_message(error), error.code) from None
            raise Unavailable(f"Bridge answered HTTP {error.code}") from None
        except (OSError, ValueError) as error:
            raise Unavailable(str(error)) from None

    def status(self):
        _, body = self._call("GET", "/integration/status")
        try:
            status = json.loads(body)
        except ValueError:
            raise Unavailable("Bridge status is not JSON") from None
        if not isinstance(status, dict):
            raise Unavailable("Bridge status is not an object")
        return status

    def action(self, body):
        _, answer = self._call("POST", "/integration/action", body)
        try:
            status = json.loads(answer)
        except ValueError:
            raise Unavailable("Bridge answer is not JSON") from None
        return status if isinstance(status, dict) else {}

    def frame(self):
        try:
            kind, body = self._call("GET", "/integration/camera/frame")
        except Refused:
            return None  # waiting for the camera
        if kind != "image/jpeg" or len(body) > MAX_FRAME_BYTES:
            return None
        return body


class HttpApi:
    def __init__(self, server, station_key, timeout=15):
        self.server, self.key, self.timeout = check_server_url(server), station_key, timeout
        self.opener = _opener()

    def _call(self, method, path, body=None, raw=None, timeout=None):
        headers = {"X-Station-Key": self.key, "Accept": "application/json"}
        data = raw
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            headers["Content-Type"] = "image/jpeg"
        request = urllib.request.Request(self.server + "/api/station/mechanism" + path,
                                         data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                answer = json.loads(response.read(1_000_000) or b"{}")
                return answer if isinstance(answer, dict) else {}
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                raise Refused(_message(error), error.code) from None
            raise Unavailable(f"API answered HTTP {error.code}") from None
        except (OSError, ValueError) as error:
            raise Unavailable(str(error)) from None

    def commands(self, wait):
        return self._call("GET", "/commands?" + urlencode({"wait": wait}), timeout=wait + 15)

    def result(self, command_id, result):
        return self._call("POST", f"/commands/{int(command_id)}/result", body=result)

    def state(self, state, remote_allowed):
        return self._call("POST", "/state", body={"state": state,
                                                  "remote_allowed": bool(remote_allowed)})

    def frame(self, jpeg):
        return self._call("POST", "/frame", raw=jpeg)


# ---- the relay -------------------------------------------------------------------------------

class Relay:
    def __init__(self, api, bridge, allow_remote, clock=monotonic):
        self.api, self.bridge, self.allow_remote, self.clock = api, bridge, allow_remote, clock
        self.lock = threading.Lock()
        self.until = float("-inf")  # heartbeats may be forwarded until then
        self.control_sent_at = float("-inf")
        self.viewer_active = False
        self.busy = False
        self.last_heartbeat = self.last_state = self.last_frame = float("-inf")

    # control information from any API answer; the newest request wins
    def control(self, answer, sent_at):
        with self.lock:
            if sent_at < self.control_sent_at:
                return
            self.control_sent_at = sent_at
            self.until = fresh_until(sent_at, answer.get("operator_fresh_for"))
            self.viewer_active = answer.get("viewer_active") is True

    def operator_fresh(self, now):
        with self.lock:
            return now < self.until

    def execute(self, command, deadline):
        action = command.get("action")
        reason = refusal(action, self.allow_remote)
        if reason:
            return {"ok": False, "error": reason}
        if not is_runnable(action, deadline, self.clock()):
            return {"ok": False, "error": "expired"}
        params = command.get("params") or {}
        if not isinstance(params, dict):
            return {"ok": False, "error": "Malformed command."}
        try:
            status = self.bridge.action({**params, "action": action})
        except Refused as error:
            return {"ok": False, "error": str(error)}
        except Unavailable:
            return {"ok": False, "error": BRIDGE_DOWN}
        self.busy = bridge_busy(status)
        return {"ok": True, "status": status}

    def handle(self, answer, sent_at):
        """Run one poll's commands and report each result."""
        self.control(answer, sent_at)
        commands = [c for c in answer.get("commands") or [] if isinstance(c, dict)]
        run, superseded = order_commands(commands)
        outcomes = [(c, {"ok": False, "error": "superseded"}) for c in superseded]
        for command in run:
            outcome = self.execute(command, command_deadline(command.get("expires_in"), sent_at))
            outcomes.append((command, outcome))
            if outcome.get("ok"):
                log.info("Ran %s", command.get("action"))
            else:
                log.warning("Did not run %s: %s", command.get("action"), outcome.get("error"))
            self.report_result(command, outcome)
        for command, outcome in outcomes[:len(superseded)]:
            self.report_result(command, outcome)
        return outcomes

    def report_result(self, command, outcome):
        try:
            self.control(self.api.result(command.get("id"), outcome), self.clock())
        except (Refused, Unavailable) as error:
            log.warning("Could not report the result of command %s: %s",
                        command.get("id"), error)

    def heartbeat_tick(self):
        now = self.clock()
        with self.lock:
            until = self.until
        if not heartbeat_due(now, until, self.last_heartbeat, self.allow_remote):
            return False
        self.last_heartbeat = now
        try:
            self.bridge.action({"action": "heartbeat"})
        except (Refused, Unavailable) as error:
            log.warning("Heartbeat not delivered to the bridge: %s", error)
        return True

    def report_tick(self):
        """Report state when due, and a preview frame when someone watches. Returns what it
        sent, for tests."""
        sent = []
        now = self.clock()
        interval = state_interval(self.busy, self.operator_fresh(now), self.viewer_active)
        if now - self.last_state >= interval:
            self.last_state = now
            try:
                status = self.bridge.status()
                self.busy = bridge_busy(status)
            except (Refused, Unavailable):
                status, self.busy = offline_state(), False
            try:
                self.control(self.api.state(status, self.allow_remote), now)
                sent.append("state")
            except (Refused, Unavailable) as error:
                log.warning("State report failed: %s", error)
        now = self.clock()
        if frame_due(now, self.last_frame, self.viewer_active):
            self.last_frame = now
            try:
                jpeg = self.bridge.frame()
            except (Refused, Unavailable):
                jpeg = None
            if jpeg and len(jpeg) <= MAX_FRAME_BYTES:
                try:
                    self.api.frame(jpeg)
                    sent.append("frame")
                except (Refused, Unavailable) as error:
                    log.warning("Preview frame upload failed: %s", error)
        return sent

    def poll_once(self, wait=POLL_WAIT):
        sent_at = self.clock()
        answer = self.api.commands(wait)
        return self.handle(answer, sent_at)

    def run(self, stopped):
        def poll_loop():
            while not stopped.is_set():
                try:
                    self.poll_once()
                except Refused as error:
                    log.error("API refused the relay (%s). Check ADGVS_STATION_KEY, "
                              "ADGVS_MECHANISM_MODE=relay and ADGVS_MECHANISM_STATION_ID on "
                              "the server.", error)
                    stopped.wait(REFUSED_RETRY_SECONDS)
                except Unavailable as error:
                    log.warning("API unreachable (%s); the bridge stops by itself.", error)
                    stopped.wait(RETRY_SECONDS)

        def report_loop():
            while not stopped.wait(0.2):
                self.report_tick()

        def heartbeat_loop():
            while not stopped.wait(0.2):
                self.heartbeat_tick()

        threads = [threading.Thread(target=target, daemon=True, name=target.__name__)
                   for target in (poll_loop, report_loop, heartbeat_loop)]
        for thread in threads:
            thread.start()
        stopped.wait()
        if self.allow_remote:
            try:  # leaving: stop explicitly rather than wait for the dead-man
                self.bridge.action({"action": "stop"})
            except (Refused, Unavailable):
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", default=os.environ.get("ADGVS_SERVER", ""),
                        help="API origin (the Render URL, not the Vercel site)")
    parser.add_argument("--bridge", default="http://127.0.0.1:8765")
    parser.add_argument("--allow-remote", action="store_true",
                        default=os.environ.get("ADGVS_MECHANISM_REMOTE") == "1",
                        help="let operators on the cloud site move the mechanism")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    station_key = os.environ.get("ADGVS_STATION_KEY", "")
    bridge_key = os.environ.get("ADGVS_MECHANISM_KEY", "")
    if not station_key or not bridge_key:
        parser.error("Set ADGVS_STATION_KEY and ADGVS_MECHANISM_KEY in the environment.")
    try:
        api = HttpApi(args.server, station_key)
        bridge = HttpBridge(args.bridge, bridge_key)
    except ValueError as error:
        parser.error(str(error))
    relay = Relay(api, bridge, args.allow_remote)
    print(f"Mechanism relay -> {api.server}; remote control "
          f"{'ENABLED' if args.allow_remote else 'off (state only; stop/disconnect)'}",
          flush=True)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    relay.run(stopped)


if __name__ == "__main__":
    main()
