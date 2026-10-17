"""One bounded Qt worker; newer submissions replace queued work and cancel old output."""

import logging
from dataclasses import dataclass, field
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ai.pipeline import Cancelled, PipelineError, check_cancelled
from services.attachments import AssistantRequest, AttachmentError, prepare_request

logger = logging.getLogger(__name__)


@dataclass
class DesktopCall:
    kind: str
    payload: object
    context: dict
    cancel: Event
    done: Event = field(default_factory=Event)
    value: object = None
    error: Exception | None = None

    def resolve(self, value=None, error=None):
        self.value, self.error = value, error
        self.done.set()


class RequestWorker(QThread):
    status = Signal(int, str)
    partial = Signal(int, str)
    completed = Signal(int, object)
    failed = Signal(int, str)
    desktop_requested = Signal(int, object)
    attachments_captured = Signal(int, object)

    def __init__(self, pipeline, request_id, prompt, history, context, parent):
        super().__init__(parent)
        self.pipeline = pipeline
        self.request_id = request_id
        self.prompt = prompt
        self.history = history
        self.context = context
        self.cancel_event = Event()

    def _desktop(self, kind, payload):
        check_cancelled(self.cancel_event)
        call = DesktopCall(kind, payload, self.context, self.cancel_event)
        self.desktop_requested.emit(self.request_id, call)
        while not call.done.wait(0.05):
            check_cancelled(self.cancel_event)
        check_cancelled(self.cancel_event)
        if call.error:
            raise call.error
        return call.value

    def run(self):
        try:
            prompt = self.prompt
            if isinstance(prompt, AssistantRequest):
                self.status.emit(self.request_id, "Reading attachments…")
                prompt = prepare_request(prompt, lambda: check_cancelled(self.cancel_event))
                self.attachments_captured.emit(self.request_id, {
                    "ids": tuple(a.id for a in prompt.attachments),
                    "truncated": tuple(a.name for a in prompt.attachments if a.truncated),
                })
            result = self.pipeline.run(
                prompt, history=self.history, cancel=self.cancel_event,
                status=lambda text: self.status.emit(self.request_id, text),
                partial=lambda text: self.partial.emit(self.request_id, text),
                desktop=self._desktop,
            )
            check_cancelled(self.cancel_event)
            self.completed.emit(self.request_id, result)
        except Cancelled:
            pass
        except (PipelineError, AttachmentError) as error:
            self.failed.emit(self.request_id, str(error))
        except Exception as error:
            logger.exception("Unexpected Mote worker failure")
            self.failed.emit(self.request_id, f"Request failed ({type(error).__name__}). Please try again.")


class PipelineController(QObject):
    status = Signal(int, str)
    partial = Signal(int, str)
    completed = Signal(int, str)
    failed = Signal(int, str)
    desktop_requested = Signal(int, object)
    attachments_captured = Signal(int, object)
    stopped = Signal()

    def __init__(self, pipeline, parent=None):
        super().__init__(parent)
        self.pipeline = pipeline
        self.history = []
        self.active_id = None
        self._serial = 0
        self._worker = None
        self._pending = None
        self._closing = False

    def submit(self, prompt, context=None):
        self.cancel()
        self._serial += 1
        self.active_id = self._serial
        self._pending = (self.active_id, prompt, context or {})
        if self._worker is None:
            self._start_pending()
        return self.active_id

    @property
    def waiting(self):
        return self._pending is not None

    def _start_pending(self):
        if self._pending is None or self._closing:
            return
        request_id, prompt, context = self._pending
        self._pending = None
        worker = RequestWorker(self.pipeline, request_id, prompt, list(self.history), context, self)
        self._worker = worker
        worker.status.connect(self._status)
        worker.partial.connect(self._partial)
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.desktop_requested.connect(self._desktop)
        worker.attachments_captured.connect(self._captured)
        worker.finished.connect(self._retire)
        worker.start()

    @Slot(int, str)
    def _status(self, request_id, text):
        if request_id == self.active_id:
            self.status.emit(request_id, text)

    @Slot(int, str)
    def _partial(self, request_id, text):
        if request_id == self.active_id:
            self.partial.emit(request_id, text)

    @Slot(int, object)
    def _completed(self, request_id, result):
        if request_id == self.active_id:
            self.history = result.history
            self.completed.emit(request_id, result.text)

    @Slot(int, str)
    def _failed(self, request_id, text):
        if request_id == self.active_id:
            self.failed.emit(request_id, text)

    @Slot(int, object)
    def _captured(self, request_id, attachments):
        if request_id == self.active_id:
            self.attachments_captured.emit(request_id, attachments)

    @Slot(int, object)
    def _desktop(self, request_id, call):
        if request_id == self.active_id and not call.cancel.is_set():
            self.desktop_requested.emit(request_id, call)
        else:
            call.resolve(error=Cancelled())

    @Slot()
    def _retire(self):
        self._worker.wait()  # finished is emitted just before QThread fully exits.
        self._worker.deleteLater()
        self._worker = None
        if self._closing:
            self.stopped.emit()
        else:
            self._start_pending()

    def cancel(self):
        self.active_id = None
        self._pending = None
        if self._worker is not None:
            self._worker.cancel_event.set()

    def request_stop(self):
        self._closing = True
        self.cancel()
        if self._worker is None:
            self.stopped.emit()

    def shutdown(self):
        """Final fallback for external QApplication.quit; never destroy a live QThread."""
        self._closing = True
        self.cancel()
        if self._worker is not None:
            self._worker.wait()
