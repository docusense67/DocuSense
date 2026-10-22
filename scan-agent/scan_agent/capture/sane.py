"""SANE page source: the Linux scan station (Raspberry Pi 5) drives its ADF scanner here.

    source = SaneSource()                       # first sheet-fed scanner found
    source = SaneSource("fujitsu:fi-7160:1234")  # or a name from `scanimage -L`

Each `pages()` call feeds one stack from the automatic document feeder: 300 DPI, 8-bit
grayscale, one side per sheet, yielded in feed order. The stack ends when the feeder runs
empty. A jam, double-feed or open cover raises `ScannerError(state, next_sequence)`; after
the operator clears it and re-feeds from that sheet, `pages(next_sequence)` continues the
numbering. A missing scanner, USB error or busy device raises `ScannerError(OFFLINE, ...)`.

Simplex on purpose: the issued documents are printed on one side, and the pipeline counts
every captured image as a page (a blank back side would read as "N pages scanned, M
issued"), so duplex would need blank-page dropping that the pipeline does not have.

python-sane is imported lazily (`pip install ".[sane]"`, Linux only) so Windows
development and the tests run without it.
"""

import importlib
import logging
from collections.abc import Iterator
from types import ModuleType

import numpy as np

from scan_agent.capture.base import CapturedPage, PageSource, ScannerError, ScannerState
from scan_agent.config import A4_MM, DPI

log = logging.getLogger("scan_agent.sane")

# sane_strstatus() texts (SANE 1.x), which python-sane raises as `_sane.error(text)`.
NO_DOCS = "document feeder out of documents"
JAMMED = "document feeder jammed"
COVER_OPEN = "scanner cover is open"
CANCELLED = "operation was cancelled"

# Read-only sensor options that some backends (e.g. fujitsu) expose after a stop.
DOUBLE_FEED_SENSORS = ("double_feed", "doublefeed", "multifeed", "multi_feed")
DOUBLE_FEED_WORDS = ("double feed", "double-feed", "multifeed", "multi-feed", "multi feed")
PAPER_SENSOR = "page_loaded"  # SANE_NAME_PAGE_LOADED, "page-loaded"

# SANE_Value_Type, stable in the SANE 1 ABI.
TYPE_BOOL, TYPE_INT, TYPE_FIXED = 0, 1, 2


def _feeder_sources(values: list) -> tuple[list[str], list[str]]:
    """Split a scanner's `source` choices into simplex and duplex feeder sources."""
    feeder = [v for v in values if isinstance(v, str)
              and any(k in v.lower() for k in ("adf", "feeder", "automatic"))]
    duplex = [v for v in feeder if any(k in v.lower() for k in ("duplex", "back", "both"))]
    return [v for v in feeder if v not in duplex], duplex


def _gray_mode(values: list) -> str | None:
    names = [v for v in values if isinstance(v, str)]
    for v in names:
        if v.lower() == "gray":
            return v
    return next((v for v in names if v.lower().startswith(("gray", "grey"))), None)


def _allows(constraint, value: float) -> bool:
    if constraint is None:
        return True
    if isinstance(constraint, tuple):  # (min, max, quant) range
        return constraint[0] <= value <= constraint[1]
    return any(abs(float(v) - value) < 0.01 for v in constraint if not isinstance(v, str))


def _clamp(constraint, value: float) -> float:
    if isinstance(constraint, tuple):
        return max(constraint[0], min(constraint[1], value))
    return value


def _coerce(option, text: str):
    """A `--sane-option NAME=VALUE` string as the option's own type."""
    kind = getattr(option, "type", None)
    if kind == TYPE_BOOL:
        return text.strip().lower() in ("1", "true", "yes", "on")
    if kind == TYPE_INT:
        return int(text)
    if kind == TYPE_FIXED:
        return float(text)
    return text


def _gray(image) -> np.ndarray:
    """A python-sane PIL image (L, or RGB if the backend ignored Gray) as uint8 grayscale."""
    if image.mode != "L":
        image = image.convert("L")
    return np.array(image, dtype=np.uint8)


class SaneSource(PageSource):
    def __init__(self, device: str | None = None, options: dict[str, str] | None = None):
        self.device_name = device  # None = the first sheet-fed scanner found
        self.options = dict(options or {})  # site overrides, applied after the defaults
        self.detail: str | None = None  # why the scanner is offline, for the dashboard
        self.dpi = DPI
        self._device_type = ""  # e.g. "sheetfed scanner", from the device list
        self._sane: ModuleType | None = None
        self._dev = None

    # ---- driver ----------------------------------------------------------------------

    def _module(self, next_sequence: int) -> ModuleType:
        if self._sane is None:
            try:
                sane = importlib.import_module("sane")
            except ImportError:
                raise self._offline(next_sequence, "Scanner driver missing: install the "
                                    "scan agent with the [sane] extra (python-sane)") from None
            try:
                sane.init()
            except Exception as e:
                raise self._offline(next_sequence, f"SANE did not start: {e}") from e
            self._sane = sane
        return self._sane

    def _errors(self) -> tuple[type[BaseException], ...]:
        sane_error = getattr(getattr(self._sane, "_sane", None), "error", None)
        # snap() raises RuntimeError("Scanner returned no data") on an empty read.
        return (sane_error, RuntimeError) if sane_error else (Exception,)

    def _offline(self, next_sequence: int, detail: str) -> ScannerError:
        self.detail = detail
        return ScannerError(ScannerState.OFFLINE, next_sequence, detail)

    def _find(self, next_sequence: int) -> str:
        sane = self._module(next_sequence)
        try:
            devices = sane.get_devices(True)  # local only: never probe the network
        except Exception as e:
            raise self._offline(next_sequence, f"Scanner list failed: {e}") from e
        types = {d[0]: str(d[3]) for d in devices}
        if self.device_name:
            if self.device_name in types:
                self._device_type = types[self.device_name]
                return self.device_name
            raise self._offline(next_sequence, f"Scanner {self.device_name} not found: check "
                                "the USB cable and power (scanimage -L)")
        if not devices:
            raise self._offline(next_sequence, "No scanner found: check the USB cable and "
                                "power (scanimage -L)")
        # Prefer a sheet-fed scanner over webcams or virtual devices.
        name = next((d[0] for kind in ("sheetfed", "scanner") for d in devices
                     if kind in str(d[3]).lower()), devices[0][0])
        self._device_type = types[name]
        return name

    def _open(self, next_sequence: int):
        if self._dev is not None:
            return self._dev
        name = self._find(next_sequence)
        try:
            dev = self._sane.open(name)
        except Exception as e:
            raise self._offline(next_sequence, f"Scanner {name} could not be opened ({e}): "
                                "is the service user in the 'scanner' group?") from e
        try:
            self._configure(dev, next_sequence)
        except ScannerError:
            dev.close()
            raise
        except Exception as e:
            dev.close()
            raise self._offline(next_sequence, f"Scanner {name} setup failed: {e}") from e
        log.info("Scanner %s ready (%s DPI)", name, self.dpi)
        self._dev = dev
        return dev

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # already gone (unplugged)
                pass
            self._dev = None

    def _configure(self, dev, next_sequence: int) -> None:
        def opt(name: str):
            # Re-read dev.opt each time: setting an option can make the backend reload them.
            o = dev.opt.get(name)
            return o if o is not None and o.is_active() and o.is_settable() else None

        def fail(detail: str) -> ScannerError:
            return self._offline(next_sequence, detail)

        if source := opt("source"):
            simplex, _duplex = _feeder_sources(list(source.constraint or []))
            if not simplex:
                raise fail(f"Scanner has no single-sided document feeder source (offers "
                           f"{source.constraint}); set one with --sane-option source=...")
            dev.source = simplex[0]
        elif "flatbed" in self._device_type.lower():
            # A flatbed would "scan" the empty glass forever: never NO_DOCS.
            raise fail(f"Scanner is not a document feeder ({self._device_type})")
        if mode := opt("mode"):
            gray = _gray_mode(list(mode.constraint or []))
            if gray is None:
                raise fail(f"Scanner has no grayscale mode ({mode.constraint})")
            dev.mode = gray
        if (depth := opt("depth")) and _allows(depth.constraint, 8):
            dev.depth = 8
        resolution = opt("resolution")
        if resolution is None or not _allows(resolution.constraint, DPI):
            raise fail(f"Scanner cannot scan at {DPI} DPI")
        dev.resolution = DPI
        # Full A4 sheet: some backends default to Letter and would cut off the footer.
        for name, mm in (("page_width", A4_MM[0]), ("page_height", A4_MM[1]),
                         ("tl_x", 0.0), ("tl_y", 0.0), ("br_x", A4_MM[0]), ("br_y", A4_MM[1])):
            if o := opt(name):
                setattr(dev, name, _clamp(o.constraint, mm))

        for raw, text in self.options.items():  # site overrides (e.g. double-feed detection)
            name = raw.replace("-", "_")
            if name not in dev.opt:
                raise fail(f"Scanner has no option {raw!r}")
            setattr(dev, name, _coerce(dev.opt[name], text))

        dpi = round(float(dev.resolution))
        if dpi != DPI:
            raise fail(f"Scanner set {dpi} DPI instead of {DPI}")
        self.dpi = dpi

    def _sensor(self, dev, names: tuple[str, ...]) -> bool | None:
        """A read-only sensor's value, or None when the backend has none of these."""
        for name in names:
            o = dev.opt.get(name)
            if o is None or not o.is_active() or o.is_settable():
                continue
            try:
                return bool(getattr(dev, name))
            except Exception:
                return None
        return None

    def _stopped(self, dev, error: BaseException, sequence: int) -> ScannerError:
        """Map a SANE status (raised as its text) to a station state."""
        text = str(error).strip()
        lower = text.lower()
        sheet = f"re-feed from sheet {sequence}"
        double_text = any(k in lower for k in DOUBLE_FEED_WORDS)
        if lower == JAMMED or "jam" in lower or double_text:
            double = double_text or self._sensor(dev, DOUBLE_FEED_SENSORS)
            if double:
                return ScannerError(ScannerState.DOUBLE_FEED, sequence,
                                    f"Double feed: clear the feeder and {sheet}")
            return ScannerError(ScannerState.JAM, sequence,
                                f"Paper jam: clear the feeder and {sheet}")
        if lower == COVER_OPEN:
            return ScannerError(ScannerState.JAM, sequence,
                                f"Scanner cover open: close it and {sheet}")
        if lower == CANCELLED:
            return ScannerError(ScannerState.JAM, sequence,
                                f"Scan stopped at the scanner: {sheet}")
        # I/O error, device busy, access denied, ...: the scanner is gone or unusable.
        # Close it so the next attempt reopens it (e.g. after replugging the cable).
        self.close()
        return self._offline(sequence, f"Scanner error ({text}): check the cable and power, "
                                       f"then {sheet}")

    # ---- PageSource ----------------------------------------------------------------------

    def status(self) -> ScannerState:
        try:
            self._find(1)
        except ScannerError:
            self.close()
            return ScannerState.OFFLINE
        self.detail = None
        return ScannerState.READY

    def paper_loaded(self) -> bool | None:
        """True/False from the feeder's paper sensor, None if the backend has none."""
        try:
            dev = self._open(1)
        except ScannerError:
            return None
        return self._sensor(dev, (PAPER_SENSOR,))

    def pages(self, start_sequence: int = 1) -> Iterator[CapturedPage]:
        dev = self._open(start_sequence)
        errors = self._errors()
        sequence = start_sequence
        try:
            while True:
                try:
                    dev.start()
                    image = dev.snap(True)  # no_cancel: keep the batch going
                except errors as e:
                    if str(e).strip().lower() == NO_DOCS:
                        return  # feeder empty: the stack is complete
                    raise self._stopped(dev, e, sequence) from e
                yield CapturedPage(sequence=sequence, image=_gray(image), dpi=self.dpi,
                                   source=f"{self.device_name or 'sane'}#{sequence}")
                sequence += 1
        finally:
            if self._dev is not None:
                try:
                    self._dev.cancel()  # end the SANE batch
                except Exception:
                    pass
