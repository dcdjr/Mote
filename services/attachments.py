"""Bounded local file preparation and structured model context. No uploads/tools."""

from dataclasses import dataclass, field
from io import BytesIO
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

TEXT_TYPES = {".txt", ".md", ".log", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_TYPES = TEXT_TYPES | IMAGE_TYPES | {".pdf"}
MAX_FILES = 6
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_CONTEXT_CHARS = 48000
MAX_FILE_CHARS = 16000
MAX_PDF_PAGES = 50


class AttachmentError(ValueError):
    pass


@dataclass(frozen=True)
class Attachment:
    path: Path
    size: int
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def name(self):
        return self.path.name


@dataclass(frozen=True)
class PreparedAttachment:
    id: str
    name: str
    file_type: str
    mime_type: str
    text: str = ""
    image: bytes | None = field(default=None, repr=False)
    truncated: bool = False


@dataclass(frozen=True)
class AssistantRequest:
    message: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class PreparedRequest:
    message: str
    attachments: tuple[PreparedAttachment, ...] = ()

    def model_message(self):
        if not self.attachments:
            return {"role": "user", "content": self.message}
        message = {"role": "user", "content": json.dumps({
            "user_message": self.message,
            "attachments": [{
                "filename": a.name, "file_type": a.file_type, "mime_type": a.mime_type,
                "text": a.text, "truncated": a.truncated,
                "image_attached": a.image is not None,
            } for a in self.attachments],
        }, ensure_ascii=False)}
        images = [a.image for a in self.attachments if a.image is not None]
        if images:
            message["images"] = images  # Ollama accepts bytes; its SDK base64-encodes them.
        return message


def select_attachments(paths, existing=()):
    """Validate selection cheaply; the worker revalidates before reading bytes."""
    selected, errors = list(existing), []
    known = {a.path for a in selected}
    for value in paths:
        path = Path(value).resolve()
        if path in known:
            continue
        try:
            if path.suffix.lower() not in SUPPORTED_TYPES:
                raise AttachmentError("Unsupported file type.")
            if not path.is_file():
                raise AttachmentError("File is missing or is not a regular file.")
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise AttachmentError("File exceeds the 10 MB limit.")
            if len(selected) >= MAX_FILES:
                raise AttachmentError("Attach up to 6 files at a time.")
            if sum(a.size for a in selected) + size > MAX_TOTAL_BYTES:
                raise AttachmentError("Attachments exceed the combined 25 MB limit.")
            selected.append(Attachment(path, size))
            known.add(path)
        except (OSError, AttachmentError) as error:
            errors.append(f"{path.name}: {error}")
    return tuple(selected), errors


def _decode(data):
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = data.decode("cp1252")
    except UnicodeError as error:
        raise AttachmentError("Cannot decode text; save it as UTF-8 or UTF-16.") from error
    if "\x00" in text:
        raise AttachmentError("This appears to be binary data, not readable text.")
    return text


def _pdf_text(data, limit, check):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise AttachmentError("PDF support requires pypdf. Install requirements.txt.") from None
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise AttachmentError("Password-protected PDFs are not supported; attach an unlocked copy.")
        pieces, count, truncated = [], 0, len(reader.pages) > MAX_PDF_PAGES
        for index, page in enumerate(reader.pages):
            check()
            if index >= MAX_PDF_PAGES:
                break
            text = page.extract_text() or ""
            remaining = limit - count
            pieces.append(text[:remaining])
            count += len(text[:remaining]) + 2
            if len(text) > remaining or count >= limit:
                truncated = True
                break
        text = "\n\n".join(pieces)
        if not text.strip():
            raise AttachmentError("PDF has no extractable text (it may be scanned). OCR is not available.")
        return text[:limit], truncated
    except AttachmentError:
        raise
    except Exception as error:
        # Cancellation belongs to the request lifecycle, not a PDF format error.
        check()
        raise AttachmentError(f"Could not read PDF ({type(error).__name__}).") from error


def _validate_image(data):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImageReader

    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if (not reader.canRead() or bytes(reader.format()) not in (b"png", b"jpeg", b"webp")
            or size.width() <= 0 or size.height() <= 0):
        raise AttachmentError("Image is invalid or cannot be decoded.")
    if size.width() * size.height() > 20_000_000:
        raise AttachmentError("Image exceeds the 20 megapixel limit; resize it first.")
    if reader.read().isNull():
        raise AttachmentError("Image is damaged or cannot be decoded.")


def prepare_request(request, check=lambda: None):
    """Called in the inference worker, never on the UI thread."""
    selected, errors = select_attachments([a.path for a in request.attachments])
    if errors or len(selected) != len(request.attachments):
        raise AttachmentError("\n".join(errors) or "Attachment selection is invalid.")
    text_count = sum(a.path.suffix.lower() not in IMAGE_TYPES for a in request.attachments)
    limit = min(MAX_FILE_CHARS, MAX_CONTEXT_CHARS // max(1, text_count))
    prepared, total_bytes = [], 0
    for attachment in request.attachments:
        check()
        try:
            with attachment.path.open("rb") as source:
                data = source.read(MAX_FILE_BYTES + 1)
            total_bytes += len(data)
            if len(data) > MAX_FILE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise AttachmentError("Files changed or exceed the attachment size limits; reselect them.")
            suffix = attachment.path.suffix.lower()
            text, image, truncated = "", None, False
            if suffix in TEXT_TYPES:
                content = _decode(data)
                text, truncated = content[:limit], len(content) > limit
            elif suffix == ".pdf":
                text, truncated = _pdf_text(data, limit, check)
            else:
                _validate_image(data)
                image = data
            prepared.append(PreparedAttachment(
                attachment.id, attachment.name, suffix,
                mimetypes.guess_type(attachment.name)[0] or "application/octet-stream",
                text, image, truncated,
            ))
        except (OSError, AttachmentError) as error:
            raise AttachmentError(f"{attachment.name}: {error}") from error
    check()
    return PreparedRequest(request.message, tuple(prepared))
