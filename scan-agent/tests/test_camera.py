"""Camera source: auto-capture (presence + stillness), recapture policy, the file, OpenCV and
Picamera2 backends (Picamera2 through a fake module), and the camera-test CLI."""

import json
import sys
import types

import cv2
import numpy as np
import pytest

from scan_agent.__main__ import main as agent_main
from scan_agent.capture import camera as camera_module
from scan_agent.capture.base import ScannerState
from scan_agent.capture.camera import CameraBackend, CameraPage, CameraSource
from scan_agent.config import A4_PX, CameraSettings, QualityThresholds
from tests.camera_frames import blur, camera_frame, default_page, empty_frame, synthetic_page

T = QualityThresholds()
PREVIEW = (480, 640)  # small preview frames (w, h)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeBackend(CameraBackend):
    """Plays a script of preview frames (the last one repeats) and still frames."""

    name = "fake"

    def __init__(self, previews, stills, settings=None):
        super().__init__(settings or CameraSettings())
        self.previews = list(previews)
        self.stills = list(stills)
        self.log: list = []

    def preview(self):
        self.log.append("preview")
        return self.previews.pop(0) if len(self.previews) > 1 else self.previews[0]

    def still(self):
        self.log.append("still")
        return self.stills.pop(0) if len(self.stills) > 1 else self.stills[0]

    def focus(self) -> str:
        self.log.append("focus")
        return "ok"

    def recover(self, issues):
        self.log.append(("recover", tuple(issues)))


def preview(shift=(0.0, 0.0), page=None):
    return camera_frame(default_page() if page is None else page, size=PREVIEW,
                        shift=shift)[0]


def source(backend, clock=None, **settings):
    clock = clock or FakeClock()
    return CameraSource(backend, CameraSettings(**{"timeout": 2.0, **settings}), T,
                        clock=clock, sleep=clock.sleep)


@pytest.fixture(scope="module")
def sharp():
    return camera_frame(default_page())[0]


# ---- auto-capture ------------------------------------------------------------------------


def test_waits_for_a_present_and_still_page_before_the_shot(sharp):
    empty = empty_frame(PREVIEW)
    moving = [preview((0.06 * i, 0.0)) for i in range(3)]  # being fed into the compartment
    backend = FakeBackend([empty] * 3 + moving + [preview()], [sharp])
    page = source(backend).capture()
    # 3 empty + 3 moving + 1 (first still frame, compared with a moving one) + 3 still.
    assert backend.log.index("still") == backend.log.count("preview") + 1
    assert backend.log.count("preview") == 3 + 3 + 1 + 3
    assert backend.log[-2:] == ["focus", "still"]
    assert isinstance(page, CameraPage) and page.quality["ok"]
    assert page.detail["settled"] and page.capture["attempts"] == 1
    assert page.image.shape == (A4_PX[1], A4_PX[0]) and page.dpi == 300


def test_timeout_without_a_page_reports_page_not_found():
    clock = FakeClock()
    backend = FakeBackend([empty_frame(PREVIEW)], [empty_frame()])
    page = source(backend, clock, timeout=1.5).capture()
    assert page.quality["issues"][-1] == "page_not_found" and not page.quality["ok"]
    assert page.capture["attempts"] == 1  # nothing to gain from shooting an empty box again
    assert not page.detail["settled"]
    assert 1500 <= page.detail["wait"] <= 1600
    assert clock.now >= 1.5


def test_recaptures_a_blurred_frame_until_usable(sharp):
    backend = FakeBackend([preview()], [blur(sharp, 2.0), blur(sharp, 2.0), sharp])
    page = source(backend).capture()
    assert page.capture["attempts"] == 3 and page.quality["ok"]
    assert backend.log.count(("recover", ("blur",))) == 2
    assert backend.log.count("focus") == 3  # refocused before every shot


def test_gives_up_after_three_attempts_and_submits_the_best(sharp):
    frames = [blur(sharp, 3.0), blur(sharp, 1.6), blur(sharp, 2.4)]
    backend = FakeBackend([preview()], frames)
    page = source(backend).capture()
    assert page.capture["attempts"] == 3
    assert page.quality["ok"] is False and page.quality["issues"] == ["blur"]
    scores = [source(FakeBackend([preview()], [f])).capture().quality["blur_score"]
              for f in frames]
    assert page.quality["blur_score"] == max(scores)


def test_no_recapture_when_the_sheet_itself_is_misplaced():
    cut = camera_frame(default_page(), shift=(0.12, 0.0))[0]
    backend = FakeBackend([preview()], [cut])
    page = source(backend).capture()
    assert page.quality["issues"] == ["page_cut_off"]
    assert page.capture["attempts"] == 1 and backend.log.count("still") == 1


def test_pages_takes_each_sheet_once_and_stops_when_the_feeder_is_empty(sharp):
    other = synthetic_page(seed=7)
    a, b = preview(), preview(page=other)
    empty = empty_frame(PREVIEW)
    # Sheet A settles; it stays a while (being screened), leaves, sheet B arrives, then
    # nothing more: the timeout ends the stack.
    previews = [a] * 4 + [a] * 5 + [empty] * 2 + [b] * 4 + [empty]
    stills = [sharp, camera_frame(other)[0]]
    backend = FakeBackend(previews, stills)
    pages = list(source(backend).pages(start_sequence=5))
    assert [p.sequence for p in pages] == [5, 6]
    assert backend.log.count("still") == 2  # sheet A was not taken twice
    assert all(p.quality["ok"] for p in pages)


# ---- file backend ------------------------------------------------------------------------


@pytest.fixture
def frames_dir(tmp_path, sharp):
    cv2.imwrite(str(tmp_path / "01.jpg"), sharp)
    cv2.imwrite(str(tmp_path / "02.png"), camera_frame(default_page(), angle=3)[0])
    cv2.imwrite(str(tmp_path / "03.png"), empty_frame())
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    return tmp_path


def test_file_source_yields_a_page_per_image_until_an_empty_frame(frames_dir):
    src = CameraSource("file", CameraSettings(source="file", device=str(frames_dir)), T)
    assert src.status() is ScannerState.READY
    pages = list(src.pages())
    assert [p.sequence for p in pages] == [1, 2]
    assert pages[0].source.endswith("01.jpg") and pages[1].source.endswith("02.png")
    for p in pages:
        assert p.capture == {"source": "file", "width": 2200, "height": 2900, "attempts": 1}
        assert p.quality["ok"] and set(p.timings_ms) == {"capture", "quality"}
    # The empty frame was looked at (and skipped), then the folder is exhausted.
    assert src.capture() is None


def test_file_source_offline_without_folder(tmp_path):
    src = CameraSource("file", CameraSettings(device=str(tmp_path / "missing")), T)
    assert src.status() is ScannerState.OFFLINE
    assert "not found" in src.detail


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="picamera2, opencv, file"):
        CameraSource("twain", CameraSettings(), T)


def test_camera_settings_from_environment(monkeypatch):
    monkeypatch.setenv("ADGVS_CAMERA_SOURCE", "opencv")
    monkeypatch.setenv("ADGVS_CAMERA_ROTATE", "90")
    monkeypatch.setenv("ADGVS_CAMERA_STILL_DIFF", "1.5")
    s = CameraSettings.from_env()
    assert (s.source, s.rotate, s.still_diff, s.focus) == ("opencv", 90, 1.5, "auto")


# ---- Picamera2 (fake module) --------------------------------------------------------------


def make_picamera2(frame, *, autofocus=True, cameras=1):
    class FakePicamera2:
        instances: list = []

        def __init__(self, number=0):
            self.number = number
            self.sensor_resolution = (frame.shape[1], frame.shape[0])
            self.camera_controls = {"ExposureTime": (1, 1000000, 10000)}
            if autofocus:
                self.camera_controls.update(AfMode=(0, 2, 0), LensPosition=(0.0, 15.0, 1.0))
            self.set: list[dict] = []
            self.af_cycles = 0
            self.closed = False
            FakePicamera2.instances.append(self)

        @staticmethod
        def global_camera_info():
            return [{"Model": "imx708", "Num": i} for i in range(cameras)]

        def create_still_configuration(self, **kwargs):
            self.config_kwargs = kwargs
            return {"config": kwargs}

        def configure(self, config):
            self.configured = config

        def start(self):
            self.started = True

        def capture_metadata(self):
            return {"ExposureTime": 20000, "AnalogueGain": 1.5, "ColourGains": (1.8, 1.4)}

        def set_controls(self, controls):
            self.set.append(dict(controls))

        def autofocus_cycle(self):
            self.af_cycles += 1
            return True

        def capture_array(self, name="main"):
            if name == "lores":
                w, h = self.config_kwargs["lores"]["size"]
                y = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (w, h),
                               interpolation=cv2.INTER_AREA)
                return np.vstack([y, np.full((h // 2, w), 128, np.uint8)])  # Y + UV planes
            return frame.copy()

        def stop(self):
            pass

        def close(self):
            self.closed = True

    return types.SimpleNamespace(Picamera2=FakePicamera2)


@pytest.fixture(scope="module")
def cm3_frame():
    """Camera Module 3 framing: the sheet across a 4608x2592 frame (BGR)."""
    gray = camera_frame(default_page(), size=(4608, 2592), page_height=1.3, angle=90)[0]
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def test_picamera2_full_resolution_still_with_autofocus_and_locked_exposure(
        monkeypatch, cm3_frame):
    fake = make_picamera2(cm3_frame)
    monkeypatch.setitem(sys.modules, "picamera2", fake)
    src = CameraSource("picamera2", CameraSettings(rotate=270, exposure="lock", awb="lock"), T)
    page = src.capture()
    cam = fake.Picamera2.instances[0]
    kwargs = cam.config_kwargs
    assert kwargs["main"] == {"size": (4608, 2592), "format": "RGB888"}
    assert kwargs["lores"] == {"size": (640, 360), "format": "YUV420"}
    assert kwargs["controls"]["AfMode"] == camera_module.AF_AUTO
    assert {"AeEnable": False, "ExposureTime": 20000, "AnalogueGain": 1.5,
            "AwbEnable": False, "ColourGains": (1.8, 1.4)} in cam.set
    assert cam.af_cycles == 1 and page.detail["focus_state"] == ["ok"]
    assert page.capture == {"source": "picamera2", "width": 4608, "height": 2592,
                            "attempts": 1}
    assert page.quality["ok"], page.quality
    assert page.quality["effective_dpi"] > 280
    assert page.source == "picamera2:0 (imx708)"
    src.close()
    assert cam.closed


def test_picamera2_fixed_focus_v2_and_manual_lens_position(monkeypatch, cm3_frame):
    fake = make_picamera2(cm3_frame, autofocus=False)
    monkeypatch.setitem(sys.modules, "picamera2", fake)
    page = CameraSource("picamera2", CameraSettings(rotate=270), T).capture()
    cam = fake.Picamera2.instances[0]
    assert "AfMode" not in cam.config_kwargs["controls"] and cam.af_cycles == 0
    assert page.detail["focus_state"] == ["fixed"]

    fake = make_picamera2(cm3_frame)
    monkeypatch.setitem(sys.modules, "picamera2", fake)
    settings = CameraSettings(rotate=270, focus="3.3", exposure="12000", gain=2.0)
    page = CameraSource("picamera2", settings, T).capture()
    cam = fake.Picamera2.instances[0]
    assert cam.config_kwargs["controls"] == {
        "AfMode": camera_module.AF_MANUAL, "LensPosition": 3.3, "AeEnable": False,
        "ExposureTime": 12000, "AnalogueGain": 2.0}
    assert cam.af_cycles == 0 and page.detail["focus_state"] == ["manual"]


def test_picamera2_missing_or_no_camera_is_offline_with_a_reason(monkeypatch, cm3_frame):
    monkeypatch.setitem(sys.modules, "picamera2", None)  # import raises ImportError
    src = CameraSource("picamera2", CameraSettings(), T)
    assert src.status() is ScannerState.OFFLINE
    assert "python3-picamera2" in src.detail and "--system-site-packages" in src.detail

    monkeypatch.setitem(sys.modules, "picamera2", make_picamera2(cm3_frame, cameras=0))
    src = CameraSource("picamera2", CameraSettings(), T)
    assert src.status() is ScannerState.OFFLINE
    assert "not found" in src.detail and "rpicam-hello --list-cameras" in src.detail


# ---- OpenCV (fake VideoCapture) ------------------------------------------------------------


def make_capture(frame, opened=True):
    class FakeCapture:
        instances: list = []

        def __init__(self, target):
            self.target = target
            self.props: dict = {}
            FakeCapture.instances.append(self)

        def isOpened(self):  # noqa: N802 - OpenCV's name
            return opened

        def set(self, prop, value):
            self.props[prop] = value
            return True

        def read(self):
            return True, frame.copy()

        def grab(self):
            return True

        def release(self):
            self.released = True

    return FakeCapture


def test_opencv_asks_for_the_largest_mjpg_mode(monkeypatch, sharp):
    fake = make_capture(sharp)
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", fake)
    page = CameraSource("opencv", CameraSettings(device="1"), T).capture()
    cap = fake.instances[0]
    assert cap.target == 1
    assert cap.props[cv2.CAP_PROP_FOURCC] == cv2.VideoWriter_fourcc(*"MJPG")
    assert cap.props[cv2.CAP_PROP_FRAME_WIDTH] == 10000
    assert page.capture["source"] == "opencv" and page.quality["ok"]

    fake = make_capture(sharp)
    monkeypatch.setattr(camera_module.cv2, "VideoCapture", fake)
    CameraSource("opencv", CameraSettings(device="rtsp://cam/stream", resolution="3840x2160"),
                 T).open()
    cap = fake.instances[0]
    assert cap.target == "rtsp://cam/stream"
    assert (cap.props[cv2.CAP_PROP_FRAME_WIDTH], cap.props[cv2.CAP_PROP_FRAME_HEIGHT]) == (
        3840, 2160)

    monkeypatch.setattr(camera_module.cv2, "VideoCapture", make_capture(sharp, opened=False))
    src = CameraSource("opencv", CameraSettings(), T)
    assert src.status() is ScannerState.OFFLINE and "not found or busy" in src.detail


# ---- CLI ---------------------------------------------------------------------------------


def test_camera_test_cli_writes_page_and_contract_json(frames_dir, tmp_path, capsys):
    out = tmp_path / "out"
    code = agent_main(["camera-test", "--source", "file", "--device", str(frames_dir),
                       "--out", str(out)])
    assert code == 0
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["capture"] == {"source": "file", "width": 2200, "height": 2900,
                                 "attempts": 1}
    assert list(result["quality"]) == ["ok", "issues", "blur_score", "brightness",
                                       "glare_fraction", "skew_deg", "coverage",
                                       "effective_dpi"]
    assert set(result["timings_ms"]) == {"capture", "quality"}
    page = cv2.imread(str(out / "page.png"), cv2.IMREAD_GRAYSCALE)
    assert page.shape == (A4_PX[1], A4_PX[0])
    assert cv2.imread(str(out / "frame.jpg")).shape[:2] == (2900, 2200)
    printed = capsys.readouterr().out
    assert "Verdict  OK" in printed and "blur_score" in printed and "Wrote" in printed


def test_camera_test_cli_exit_codes(tmp_path, sharp):
    blurry = tmp_path / "blurry"
    blurry.mkdir()
    cv2.imwrite(str(blurry / "a.png"), blur(sharp, 3.0))
    out = tmp_path / "out"
    assert agent_main(["camera-test", "--source", "file", "--device", str(blurry),
                       "--out", str(out), "--no-frame"]) == 1
    assert json.loads((out / "result.json").read_text(encoding="utf-8"))[
        "quality"]["issues"] == ["blur"]
    assert not (out / "frame.jpg").exists()
    assert agent_main(["camera-test", "--source", "file", "--device",
                       str(tmp_path / "missing"), "--out", str(out)]) == 2
