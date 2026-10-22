"""Camera page source: the camera compartment station (feeder -> compartment -> camera).

    source = CameraSource()                        # settings from ADGVS_CAMERA_* (config.py)
    source = CameraSource("opencv", CameraSettings(device="0"))   # USB webcam on a dev PC
    page = source.capture()                        # one CameraPage, or None (no images left)

Backends (ADGVS_CAMERA_SOURCE):
- `picamera2`: Raspberry Pi Camera Module 3 (12 MP, autofocus) or V2 (8 MP, fixed focus)
  through libcamera. Full-resolution still configuration with a small YUV `lores` stream
  for the stillness check, so taking the shot needs no mode switch. Picamera2 comes from
  apt (python3-picamera2), not pip: the station venv needs --system-site-packages
  (deploy/station/pi-setup.sh --camera). Imported lazily so Windows dev and CI never need it.
- `opencv`: cv2.VideoCapture (device index or stream URL), asking for the largest
  resolution the camera offers (MJPG, so 4K USB cameras are not limited by USB bandwidth).
- `file`: images from a folder, one page per file (tests, dev, replaying real captures).

One capture: wait until a page is present and the frame is still (preview frames whose
mean difference stays below `still_diff` for `still_frames` frames), focus, take the
full-resolution still, find + flatten the page (capture/page.py) and measure its quality
(quality.py). An unusable frame for a reason a new shot can fix (blur, exposure, glare) is
captured again, up to MAX_CAPTURE_ATTEMPTS in total; then the best attempt is returned
with quality.ok = false. That is capture QA; screening decisions belong to the backend.
"""

import importlib
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scan_agent.capture.base import CapturedPage, PageSource, ScannerState
from scan_agent.capture.page import detect_page
from scan_agent.config import MAX_CAPTURE_ATTEMPTS, CameraSettings, QualityThresholds
from scan_agent.processing.cleanup import to_gray
from scan_agent.quality import TRANSIENT_ISSUES, inspect

log = logging.getLogger("scan_agent.camera")

SOURCES = ("picamera2", "opencv", "file")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
# libcamera AfModeEnum values (Manual, Auto): plain ints, so libcamera need not be imported.
AF_MANUAL, AF_AUTO = 0, 1
SETTLE_FRAMES = 8  # frames for auto-exposure / white balance to converge after a change
POLL_S = 0.03  # pause between preview frames (the camera's own frame rate also paces them)


class CameraError(Exception):
    """The camera cannot be used (missing driver, unplugged, busy). The message is the
    operator-facing detail for the dashboard / logs."""


def _gray_small(frame: np.ndarray, width: int) -> np.ndarray:
    gray = to_gray(frame)
    if gray.shape[1] <= width:
        return gray
    height = max(1, round(gray.shape[0] * width / gray.shape[1]))
    return cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)


# ---- backends ----------------------------------------------------------------------------


class CameraBackend(ABC):
    name = ""
    live = True  # False: the same frame until release() (file backend)

    def __init__(self, settings: CameraSettings):
        self.settings = settings
        self.size: tuple[int, int] | None = None  # full-resolution (w, h) once open

    def open(self) -> None:  # noqa: B027 - optional hook
        """Start the camera. Raise CameraError with an operator-facing reason."""

    def close(self) -> None:  # noqa: B027 - optional hook
        pass

    @abstractmethod
    def preview(self) -> np.ndarray | None:
        """A small grayscale frame for presence/stillness; None when there is nothing to
        show at all (file backend: no images left)."""

    @abstractmethod
    def still(self) -> np.ndarray:
        """A full-resolution frame (BGR or gray)."""

    def focus(self) -> str:
        """Focus before a still: 'ok'/'failed' (autofocus cycle), 'manual' (fixed lens
        position), 'fixed' (no autofocus lens) or 'n/a'."""
        return "n/a"

    def recover(self, issues: list[str]) -> None:  # noqa: B027 - optional hook
        """Before a recapture: e.g. re-meter the exposure after too_dark/too_bright."""

    def release(self) -> None:  # noqa: B027 - optional hook
        """The page was taken (file backend: move on to the next image)."""

    def describe(self) -> str:
        return self.name


class Picamera2Backend(CameraBackend):
    name = "picamera2"

    def __init__(self, settings: CameraSettings):
        super().__init__(settings)
        self.cam = None
        self.number = int(settings.device or 0)
        self.lores: tuple[int, int] = (settings.preview_width, settings.preview_width * 3 // 4)
        self.autofocus = False
        self.model = ""

    def _module(self):
        try:
            return importlib.import_module("picamera2")
        except ImportError:
            raise CameraError(
                "Picamera2 missing: install python3-picamera2 (apt) and create the venv with "
                "--system-site-packages (sudo pi-setup.sh --camera)") from None

    def _controls(self) -> dict:
        s = self.settings
        controls: dict = {}
        if self.autofocus:
            if s.focus.strip().lower() == "auto":
                controls["AfMode"] = AF_AUTO
            else:
                controls.update(AfMode=AF_MANUAL, LensPosition=float(s.focus))
        exposure = s.exposure.strip().lower()
        if exposure not in ("auto", "lock"):
            controls.update(AeEnable=False, ExposureTime=int(float(exposure)),
                            AnalogueGain=float(s.gain or 1.0))
        awb = s.awb.strip().lower()
        if awb not in ("auto", "lock"):
            red, blue = (float(v) for v in awb.split(","))
            controls.update(AwbEnable=False, ColourGains=(red, blue))
        return controls

    def open(self) -> None:
        if self.cam is not None:
            return
        module = self._module()
        picamera2 = module.Picamera2
        try:
            cameras = list(picamera2.global_camera_info())
        except Exception as e:
            raise CameraError(f"libcamera did not start: {e}") from e
        if len(cameras) <= self.number:
            raise CameraError(f"Camera {self.number} not found ({len(cameras)} connected): "
                              "check the ribbon cable (rpicam-hello --list-cameras)")
        self.model = str(cameras[self.number].get("Model", ""))
        cam = None
        try:
            cam = picamera2(self.number)
            self.size = tuple(int(v) for v in cam.sensor_resolution)
            w, h = self.size
            # Same aspect as the sensor so the preview shows the whole field of view.
            self.lores = (self.settings.preview_width,
                          round(self.settings.preview_width * h / w / 2) * 2)
            self.autofocus = "AfMode" in cam.camera_controls
            config = cam.create_still_configuration(
                main={"size": self.size, "format": "RGB888"},
                lores={"size": self.lores, "format": "YUV420"},
                buffer_count=2, display=None, controls=self._controls())
            cam.configure(config)
            cam.start()
        except Exception as e:
            if cam is not None:
                try:
                    cam.close()
                except Exception:
                    pass
            raise CameraError(f"Camera {self.number} could not be started ({e}): is another "
                              "program using it?") from e
        self.cam = cam
        self._settle()
        log.info("Camera %s (%s) %dx%d, autofocus %s, focus=%s exposure=%s awb=%s",
                 self.number, self.model or "?", *self.size,
                 "yes" if self.autofocus else "no (fixed focus)", self.settings.focus,
                 self.settings.exposure, self.settings.awb)

    def _settle(self) -> None:
        """Let auto-exposure / white balance converge, then lock them if configured."""
        for _ in range(SETTLE_FRAMES):
            self.cam.capture_metadata()
        lock: dict = {}
        metadata = self.cam.capture_metadata()
        if self.settings.exposure.strip().lower() == "lock":
            lock.update(AeEnable=False, ExposureTime=int(metadata["ExposureTime"]),
                        AnalogueGain=float(metadata["AnalogueGain"]))
        if self.settings.awb.strip().lower() == "lock" and "ColourGains" in metadata:
            lock.update(AwbEnable=False, ColourGains=tuple(metadata["ColourGains"]))
        if lock:
            self.cam.set_controls(lock)
            log.info("Exposure locked: %s", {k: v for k, v in lock.items() if k != "AeEnable"})

    def close(self) -> None:
        if self.cam is not None:
            try:
                self.cam.stop()
                self.cam.close()
            except Exception:
                pass
            self.cam = None

    def preview(self) -> np.ndarray:
        yuv = self.cam.capture_array("lores")
        w, h = self.lores
        return np.ascontiguousarray(yuv[:h, :w])  # the Y plane is the grayscale image

    def still(self) -> np.ndarray:
        frame = self.cam.capture_array("main")  # "RGB888" is BGR byte order, as OpenCV wants
        return frame[:, :, :3] if frame.ndim == 3 and frame.shape[2] == 4 else frame

    def focus(self) -> str:
        if not self.autofocus:
            return "fixed"
        if self.settings.focus.strip().lower() != "auto":
            return "manual"
        return "ok" if self.cam.autofocus_cycle() else "failed"

    def recover(self, issues: list[str]) -> None:
        if {"too_dark", "too_bright"} & set(issues) and \
                self.settings.exposure.strip().lower() == "lock":
            self.cam.set_controls({"AeEnable": True})
            self._settle()

    def describe(self) -> str:
        return f"picamera2:{self.number}" + (f" ({self.model})" if self.model else "")


class OpenCVBackend(CameraBackend):
    name = "opencv"
    FLUSH = 2  # buffered frames dropped before a still (they predate the stillness check)

    def __init__(self, settings: CameraSettings):
        super().__init__(settings)
        device = (settings.device or "0").strip()
        self.target: int | str = int(device) if device.lstrip("-").isdigit() else device
        self.cap = None

    def open(self) -> None:
        if self.cap is not None:
            return
        cap = cv2.VideoCapture(self.target)
        if not cap.isOpened():
            raise CameraError(f"Camera {self.target!r} not found or busy (OpenCV)")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if self.settings.resolution:
            w, h = (int(v) for v in self.settings.resolution.lower().split("x"))
        else:
            w, h = 10000, 10000  # drivers clamp to the largest mode they support
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)  # ignored by fixed-focus cameras
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise CameraError(f"Camera {self.target!r} opened but sends no frames")
        self.cap = cap
        self.size = (frame.shape[1], frame.shape[0])
        log.info("Camera %r (OpenCV) %dx%d", self.target, *self.size)

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def _read(self) -> np.ndarray:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise CameraError(f"Camera {self.target!r} stopped sending frames")
        return frame

    def preview(self) -> np.ndarray:
        return _gray_small(self._read(), self.settings.preview_width)

    def still(self) -> np.ndarray:
        for _ in range(self.FLUSH):
            self.cap.grab()
        return self._read()

    def describe(self) -> str:
        return f"opencv:{self.target}"


class FileBackend(CameraBackend):
    """Each image in the folder (name order) is one page in the compartment."""

    name = "file"
    live = False

    def __init__(self, settings: CameraSettings):
        super().__init__(settings)
        self.folder = Path(settings.device or ".")
        self.files: list[Path] | None = None
        self.index = 0
        self._image: np.ndarray | None = None

    def open(self) -> None:
        if self.files is not None:
            return
        if not self.folder.is_dir():
            raise CameraError(f"Image folder {self.folder} not found")
        self.files = sorted(p for p in self.folder.iterdir()
                            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)

    @property
    def current(self) -> Path | None:
        return self.files[self.index] if self.files and self.index < len(self.files) else None

    def _load(self) -> np.ndarray | None:
        if self._image is None and self.current is not None:
            image = cv2.imread(str(self.current), cv2.IMREAD_COLOR)
            if image is None:
                raise CameraError(f"Unreadable image: {self.current}")
            self._image = image
            self.size = (image.shape[1], image.shape[0])
        return self._image

    def preview(self) -> np.ndarray | None:
        image = self._load()
        return None if image is None else _gray_small(image, self.settings.preview_width)

    def still(self) -> np.ndarray:
        image = self._load()
        if image is None:
            raise CameraError(f"No images left in {self.folder}")
        return image

    def release(self) -> None:
        self.index += 1
        self._image = None

    def describe(self) -> str:
        return f"file:{self.current or self.folder}"


BACKENDS: dict[str, type[CameraBackend]] = {
    "picamera2": Picamera2Backend, "opencv": OpenCVBackend, "file": FileBackend}


def make_backend(name: str, settings: CameraSettings) -> CameraBackend:
    if name not in BACKENDS:
        raise ValueError(f"Unknown camera source {name!r}: one of {', '.join(SOURCES)}")
    return BACKENDS[name](settings)


# ---- source ------------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraPage(CapturedPage):
    """A captured page plus the contract's `capture` and `quality` blocks."""

    capture: dict = field(default_factory=dict)  # {"source", "width", "height", "attempts"}
    quality: dict = field(default_factory=dict)  # quality.assess_page() block
    timings_ms: dict = field(default_factory=dict)  # {"capture", "quality"}
    detail: dict = field(default_factory=dict)  # diagnostics: wait, focus, corners, ...
    frame: np.ndarray | None = None  # the chosen full-resolution frame, if kept


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


class CameraSource(PageSource):
    def __init__(self, backend: CameraBackend | str | None = None,
                 settings: CameraSettings | None = None,
                 thresholds: QualityThresholds | None = None, *,
                 max_attempts: int = MAX_CAPTURE_ATTEMPTS, keep_frame: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings or CameraSettings.from_env()
        if backend is None or isinstance(backend, str):
            backend = make_backend(backend or self.settings.source, self.settings)
        self.backend = backend
        self.thresholds = thresholds or QualityThresholds.from_env()
        self.max_attempts = max(1, max_attempts)
        self.keep_frame = keep_frame
        self.clock = clock
        self.sleep = sleep
        self.detail: str | None = None  # why the camera is offline, for the dashboard
        self._opened = False

    # ---- lifecycle -------------------------------------------------------------------

    def open(self) -> None:
        if not self._opened:
            try:
                self.backend.open()
            except CameraError as e:
                self.detail = str(e)
                raise
            self._opened = True
            self.detail = None

    def close(self) -> None:
        self.backend.close()
        self._opened = False

    def status(self) -> ScannerState:
        try:
            self.open()
        except CameraError:
            return ScannerState.OFFLINE
        return ScannerState.READY

    # ---- auto-capture ----------------------------------------------------------------

    def wait_for_page(self, timeout: float | None = None,
                      after: np.ndarray | None = None) -> np.ndarray | None:
        """Wait until a page is present and still. Returns the last (smoothed) preview
        frame, or None on timeout / nothing to show. With `after` (the preview of the page
        taken last), a page counts only once the compartment emptied or clearly changed,
        so the same sheet is never taken twice."""
        s = self.settings
        deadline = self.clock() + (s.timeout if timeout is None else timeout)
        previous: np.ndarray | None = None
        still = 0
        need_change = after is not None and self.backend.live
        while True:
            small = self.backend.preview()
            if small is None:
                return None
            smooth = cv2.GaussianBlur(small, (5, 5), 0)
            diff = (float(cv2.absdiff(smooth, previous).mean())
                    if previous is not None and previous.shape == smooth.shape else None)
            previous = smooth
            present = detect_page(small, min_coverage=self.thresholds.coverage_min,
                                  refine=False).found
            if not present and not self.backend.live:
                return None  # a stored image never changes: no point waiting
            if need_change:
                if not present or (after.shape == smooth.shape and
                                   float(cv2.absdiff(smooth, after).mean()) > s.change_diff):
                    need_change = False
                still = 0
            elif present and diff is not None and diff < s.still_diff:
                still += 1
                if still >= s.still_frames:
                    return smooth
            else:
                still = 0
            if self.clock() >= deadline:
                return None
            self.sleep(POLL_S)

    def capture(self, sequence: int = 1, *, wait: bool = True, timeout: float | None = None,
                after: np.ndarray | None = None,
                shoot_on_timeout: bool = True) -> CameraPage | None:
        """Capture the page in the compartment. On a wait timeout the still is taken anyway
        and reported as it is (page_not_found for an empty compartment), unless
        `shoot_on_timeout` is off; then, and when the source has no frame at all (file
        backend: no images left), the result is None."""
        self.open()
        start = self.clock()
        preview = self.wait_for_page(timeout, after) if wait else None
        waited = self.clock() - start
        if wait and preview is None and (not shoot_on_timeout
                                         or self.backend.preview() is None):
            if not self.backend.live and self.backend.preview() is not None:
                self.backend.release()  # a stored image without a page: skip it
            return None
        settled = preview is not None or not wait

        best = None
        timings = {"wait": _ms(waited), "focus": 0, "still": 0, "quality": 0}
        focus_states: list[str] = []
        attempts = 0
        issues: list[str] = []
        while attempts < self.max_attempts:
            attempts += 1
            if attempts > 1:
                self.backend.recover(issues)
            t = self.clock()
            focus_states.append(self.backend.focus())
            timings["focus"] += _ms(self.clock() - t)
            t = self.clock()
            frame = self.backend.still()
            timings["still"] += _ms(self.clock() - t)
            t = self.clock()
            result = inspect(frame, rotate=self.settings.rotate,
                             keep_aspect=self.settings.page_aspect.strip().lower() == "keep",
                             thresholds=self.thresholds)
            timings["quality"] += _ms(self.clock() - t)
            issues = result.quality["issues"]
            rank = (len(issues), -result.quality["blur_score"])
            if best is None or rank < best[0]:
                best = (rank, frame, result)
            log.info("Page %d attempt %d: %s (blur %.0f, brightness %.2f, glare %.4f, "
                     "skew %.1f, coverage %.2f, %d DPI)", sequence, attempts,
                     ", ".join(issues) or "ok", result.quality["blur_score"],
                     result.quality["brightness"], result.quality["glare_fraction"],
                     result.quality["skew_deg"], result.quality["coverage"],
                     result.quality["effective_dpi"])
            if result.quality["ok"] or not settled or not TRANSIENT_ISSUES & set(issues):
                break

        _, frame, result = best
        source = self.backend.describe()
        if settled or not self.backend.live:
            self.backend.release()
        capture_ms = timings["wait"] + timings["focus"] + timings["still"]
        detail = {**timings, "focus_state": focus_states, "settled": settled,
                  "corners": None if result.detection.corners is None
                  else np.round(result.detection.corners, 1).tolist(),
                  "sideways": result.detection.sideways, "preview": preview}
        log.info("Page %d captured in %d ms (wait %d, focus %s %d, still %d) + quality %d ms, "
                 "%d attempt(s): %s", sequence, capture_ms, timings["wait"],
                 "/".join(focus_states), timings["focus"], timings["still"],
                 timings["quality"], attempts, "ok" if result.quality["ok"]
                 else ", ".join(result.quality["issues"]))
        return CameraPage(
            sequence=sequence, image=result.page, dpi=300, source=source,
            capture={"source": self.backend.name, "width": int(frame.shape[1]),
                     "height": int(frame.shape[0]), "attempts": attempts},
            quality=result.quality,
            timings_ms={"capture": capture_ms, "quality": timings["quality"]},
            detail=detail, frame=frame if self.keep_frame else None)

    # ---- PageSource ------------------------------------------------------------------

    def pages(self, start_sequence: int = 1) -> Iterator[CameraPage]:
        """Pages as the feeder brings them: each one once it is present and still, until
        no new page arrives within the timeout (the feeder is empty). Each page must leave
        (or be replaced) before the next is taken; the caller routes it in between."""
        sequence = start_sequence
        after = None
        while True:
            page = self.capture(sequence, after=after, shoot_on_timeout=False)
            if page is None:
                return
            yield page
            after = page.detail["preview"]
            sequence += 1
