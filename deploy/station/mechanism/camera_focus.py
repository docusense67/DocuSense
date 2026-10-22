"""Capability-aware Picamera2 autofocus; never assumes a motorized lens exists."""
import threading
from time import monotonic


class FocusError(RuntimeError):
    pass


class FocusCancelled(RuntimeError):
    pass


class CameraFocus:
    def __init__(self):
        self.lock = threading.Lock()
        self.supported = None
        self.state = 'unchecked'
        self.message = 'Open camera preview to check autofocus support.'

    def mark(self, state, message, supported=None):
        with self.lock:
            self.state, self.message = state, message
            if supported is not None:
                self.supported = supported

    def status(self):
        with self.lock:
            return {'supported': self.supported, 'state': self.state, 'message': self.message}

    def configure(self, camera):
        available = camera.camera_controls
        if 'AfMode' not in available or 'AfTrigger' not in available:
            self.mark('manual', 'This camera has no autofocus controls. Adjust its lens or camera distance manually.',
                      supported=False)
            return
        from libcamera import controls
        settings = {'AfMode': controls.AfModeEnum.Continuous}
        if 'AfRange' in available:
            settings['AfRange'] = controls.AfRangeEnum.Full
        camera.set_controls(settings)
        self.mark('continuous', 'Continuous autofocus enabled for the live camera.', supported=True)

    def prepare_capture(self, camera, cancelled, timeout=4):
        """Caller holds camera I/O lock. Keep focus locked for the document capture."""
        if not self.status()['supported']:
            return  # Manual/fixed-focus cameras still capture; never claim focus was verified.
        self.mark('focusing', 'Focusing on the stopped paper…')
        job = None
        finished = False
        try:
            job = camera.autofocus_cycle(wait=False)
            deadline = monotonic() + timeout
            while True:
                if cancelled.is_set():
                    raise FocusCancelled()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise FocusError('Autofocus timed out. Adjust camera distance and lighting, then try again.')
                try:
                    focused = camera.wait(job, timeout=min(.1, remaining))
                except TimeoutError:
                    continue
                finished = True
                if not focused:
                    raise FocusError('Autofocus could not lock onto the paper. Adjust distance and lighting, then try again.')
                self.mark('focused', 'Autofocus locked for the paper capture.')
                return
        except FocusCancelled:
            self.mark('cancelled', 'Autofocus stopped.')
            raise
        except FocusError as error:
            self.mark('failed', str(error))
            raise
        except Exception as error:
            message = 'Camera autofocus failed. Check the camera and lens controls.'
            self.mark('failed', message)
            raise FocusError(message) from error
        finally:
            if job is not None and not finished:
                # A timed-out job must not stay queued ahead of later camera captures.
                camera.cancel_all_and_flush()
