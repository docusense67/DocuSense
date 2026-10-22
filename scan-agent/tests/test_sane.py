"""SANE source against a fake `sane` module: options, feed order, jams, resume, offline."""

import sys
from types import ModuleType

import numpy as np
import pytest
from PIL import Image

from scan_agent.capture.base import ScannerError, ScannerState
from scan_agent.capture.sane import SaneSource

NO_DOCS = "Document feeder out of documents"
JAMMED = "Document feeder jammed"


class FakeSaneError(Exception):
    pass


class FakeOption:
    def __init__(self, name, constraint=None, type_=1, settable=True, active=True):
        self.py_name = name
        self.constraint = constraint
        self.type = type_
        self._settable = settable
        self._active = active

    def is_active(self):
        return self._active

    def is_settable(self):
        return self._settable


def page(value: int, mode: str = "L") -> Image.Image:
    return Image.new(mode, (40, 60), value if mode == "L" else (value,) * 3)


class FakeDevice:
    """Mimics python-sane's SaneDev: options as attributes, start()/snap() per sheet.

    `feed` is the paper in the feeder: an image is a sheet, a string is the SANE status
    that start() raises at that point (e.g. a jam)."""

    def __init__(self, feed, options=None):
        self.__dict__.update(feed=list(feed), values={}, cancelled=0, closed=False,
                             opt=options if options is not None else default_options())

    def __setattr__(self, name, value):
        if name in self.opt:
            self.values[name] = value
        else:
            self.__dict__[name] = value

    def __getattr__(self, name):
        values = self.__dict__["values"]
        if name in values:
            return values[name]
        if name in self.__dict__["opt"]:
            return self.__dict__.get("sensors", {}).get(name, 0)
        raise AttributeError(name)

    def start(self):
        if not self.feed:
            raise FakeSaneError(NO_DOCS)
        sheet = self.feed.pop(0)
        if isinstance(sheet, str):
            raise FakeSaneError(sheet)
        self.__dict__["current"] = sheet

    def snap(self, no_cancel=False):
        return self.__dict__["current"]

    def cancel(self):
        self.__dict__["cancelled"] += 1

    def close(self):
        self.__dict__["closed"] = True


def default_options():
    return {
        "source": FakeOption("source", ["Flatbed", "ADF Front", "ADF Back", "ADF Duplex"], 3),
        "mode": FakeOption("mode", ["Lineart", "Halftone", "Gray", "Color"], 3),
        "depth": FakeOption("depth", [1, 8, 16]),
        "resolution": FakeOption("resolution", [150, 200, 300, 600]),
        "page_height": FakeOption("page_height", (0.0, 355.6, 0.0), 2),
        "br_x": FakeOption("br_x", (0.0, 215.9, 0.0), 2),
        "br_y": FakeOption("br_y", (0.0, 355.6, 0.0), 2),
        "double_feed": FakeOption("double_feed", None, 0, settable=False),
        "page_loaded": FakeOption("page_loaded", None, 0, settable=False),
    }


@pytest.fixture
def fake_sane(monkeypatch):
    """Install a fake `sane` module; `fake.devices` / `fake.device` set up the scanner."""
    module = ModuleType("sane")
    module._sane = ModuleType("_sane")
    module._sane.error = FakeSaneError
    module.devices = [("fujitsu:fi-7160:1", "FUJITSU", "fi-7160", "sheetfed scanner")]
    module.device = FakeDevice([])
    module.opened = []

    module.init = lambda: (1, 1, 0, 0)
    module.get_devices = lambda local_only=False: list(module.devices)

    def open_(name):
        if name not in [d[0] for d in module.devices]:
            raise FakeSaneError("Invalid argument")
        module.opened.append(name)
        return module.device

    module.open = open_
    monkeypatch.setitem(sys.modules, "sane", module)
    return module


def test_normal_stack_in_feed_order(fake_sane):
    fake_sane.device = FakeDevice([page(10), page(20), page(30, "RGB")])
    source = SaneSource()

    pages = list(source.pages())

    assert [p.sequence for p in pages] == [1, 2, 3]
    assert [int(p.image[0, 0]) for p in pages] == [10, 20, 30]
    assert all(p.image.dtype == np.uint8 and p.image.ndim == 2 for p in pages)  # RGB -> gray
    assert all(p.dpi == 300 for p in pages)
    values = fake_sane.device.values
    assert values["source"] == "ADF Front"  # simplex feeder, not the flatbed or duplex
    assert values["mode"] == "Gray" and values["depth"] == 8 and values["resolution"] == 300
    assert values["page_height"] == 297.0 and values["br_y"] == 297.0  # A4, not Letter
    assert fake_sane.device.cancelled == 1  # batch ended
    assert source.status() is ScannerState.READY


def test_empty_feeder_yields_nothing(fake_sane):
    fake_sane.device = FakeDevice([])
    assert list(SaneSource().pages()) == []


def test_jam_mid_stack_stops_with_next_sheet(fake_sane):
    fake_sane.device = FakeDevice([page(1), page(2), JAMMED, page(4)])
    source = SaneSource()
    got = []

    with pytest.raises(ScannerError) as stop:
        for p in source.pages():
            got.append(p.sequence)

    assert got == [1, 2]
    assert stop.value.state is ScannerState.JAM
    assert stop.value.next_sequence == 3
    assert "re-feed from sheet 3" in str(stop.value)
    assert fake_sane.device.cancelled == 1


def test_resume_continues_numbering_from_start_sequence(fake_sane):
    fake_sane.device = FakeDevice([page(3), page(4)])  # operator re-fed sheets 3 and 4
    pages = list(SaneSource().pages(start_sequence=3))
    assert [p.sequence for p in pages] == [3, 4]


@pytest.mark.parametrize("sensor, message, state", [
    (1, JAMMED, ScannerState.DOUBLE_FEED),          # backend sensor says double feed
    (0, "Double feed detected", ScannerState.DOUBLE_FEED),  # backend message says so
    (0, JAMMED, ScannerState.JAM),
    (0, "Scanner cover is open", ScannerState.JAM),
    (0, "Operation was cancelled", ScannerState.JAM),
    (0, "Error during device I/O", ScannerState.OFFLINE),
    (0, "Device busy", ScannerState.OFFLINE),
])
def test_stop_reasons_map_to_station_states(fake_sane, sensor, message, state):
    fake_sane.device = FakeDevice([page(1), message])
    fake_sane.device.__dict__["sensors"] = {"double_feed": sensor}
    source = SaneSource()

    with pytest.raises(ScannerError) as stop:
        list(source.pages())

    assert stop.value.state is state
    assert stop.value.next_sequence == 2
    assert stop.value.state.value in {"ready", "scanning", "jam", "double_feed", "offline"}
    if state is ScannerState.OFFLINE:
        assert fake_sane.device.closed  # reopened on the next attempt (e.g. replugged)


def test_no_device_is_offline(fake_sane):
    fake_sane.devices = []
    source = SaneSource()

    assert source.status() is ScannerState.OFFLINE
    assert "No scanner found" in source.detail
    with pytest.raises(ScannerError) as stop:
        list(source.pages(start_sequence=5))
    assert stop.value.state is ScannerState.OFFLINE
    assert stop.value.next_sequence == 5


def test_named_device_missing_is_offline(fake_sane):
    source = SaneSource("epson2:libusb:001:004")
    assert source.status() is ScannerState.OFFLINE
    assert "epson2:libusb:001:004 not found" in source.detail


def test_without_python_sane_is_offline(monkeypatch):
    monkeypatch.setitem(sys.modules, "sane", None)  # import sane -> ImportError
    source = SaneSource()
    assert source.status() is ScannerState.OFFLINE
    assert "python-sane" in source.detail


def test_prefers_sheetfed_scanner(fake_sane):
    fake_sane.devices = [("v4l:/dev/video0", "Noname", "webcam", "video camera"),
                         ("canon_dr:libusb:001:005", "CANON", "DR-C225", "sheetfed scanner")]
    list(SaneSource().pages())
    assert fake_sane.opened == ["canon_dr:libusb:001:005"]


def test_cannot_scan_300_dpi_is_offline(fake_sane):
    options = default_options()
    options["resolution"] = FakeOption("resolution", [150, 200, 600])
    fake_sane.device = FakeDevice([page(1)], options)
    with pytest.raises(ScannerError) as stop:
        list(SaneSource().pages())
    assert stop.value.state is ScannerState.OFFLINE
    assert "300 DPI" in str(stop.value)


def test_flatbed_without_feeder_is_refused(fake_sane):
    fake_sane.devices = [("genesys:libusb:001:006", "Canon", "LiDE 220", "flatbed scanner")]
    options = default_options()
    del options["source"]
    fake_sane.device = FakeDevice([page(1)], options)
    with pytest.raises(ScannerError, match="not a document feeder"):
        list(SaneSource().pages())


def test_site_options_override_defaults(fake_sane):
    options = default_options()
    options["df_action"] = FakeOption("df_action", ["Default", "Continue", "Stop"], 3)
    options["swdeskew"] = FakeOption("swdeskew", None, 0)
    fake_sane.device = FakeDevice([page(1)], options)

    list(SaneSource(options={"source": "ADF Back", "df-action": "Stop",
                             "swdeskew": "yes"}).pages())

    values = fake_sane.device.values
    assert values["source"] == "ADF Back" and values["df_action"] == "Stop"
    assert values["swdeskew"] is True


def test_unknown_site_option_is_offline(fake_sane):
    fake_sane.device = FakeDevice([page(1)])
    with pytest.raises(ScannerError, match="no option 'bogus'"):
        list(SaneSource(options={"bogus": "1"}).pages())


def test_paper_sensor(fake_sane):
    fake_sane.device = FakeDevice([])
    source = SaneSource()
    assert source.paper_loaded() is False
    fake_sane.device.__dict__["sensors"] = {"page_loaded": 1}
    assert source.paper_loaded() is True
    del fake_sane.device.opt["page_loaded"]
    assert source.paper_loaded() is None  # no sensor: the station just tries to feed
