"""Reusable Ollama backend. Only answer content crosses the streaming boundary."""

import os
from collections.abc import Iterator

from ollama import Client, ResponseError
from ai.runtime import keep_alive, model_options

MODEL = os.getenv("MOTE_MODEL", "gemma4:e2b")


class GemmaBackend:
    def __init__(self, *, client=None, model=None):
        self.model = model or os.getenv("MOTE_MODEL", "gemma4:e2b")
        self.client = client or Client(
            host=os.getenv("MOTE_OLLAMA_HOST", "http://localhost:11434"),
            timeout=float(os.getenv("MOTE_TIMEOUT", "60")),
        )
        self._info = None

    def model_info(self):
        if self._info is None:
            info = self.client.show(self.model)
            self._info = info if isinstance(info, dict) else info.model_dump()
        return self._info

    def supports_vision(self):
        return "vision" in self.model_info().get("capabilities", [])

    def _thinking_options(self, reasoning, notice):
        info = self.model_info()
        values = (info.get("thinking") or {}).get("values", [])
        if any(type(value) is bool and value == reasoning for value in values):
            return {"think": reasoning}
        if values:
            # Named levels cannot necessarily be disabled. Do not invent a
            # mapping from a boolean routing decision to a model-specific level.
            if reasoning:
                notice("Thinking · model default reasoning")
            return {}
        if "thinking" in info.get("capabilities", []):
            return {"think": reasoning}
        if reasoning:
            notice("Thinking · this model has no reasoning control")
        return {}

    def stream_generate(self, messages, *, reasoning=False, notice=lambda text: None) -> Iterator[str]:
        options = self._thinking_options(reasoning, notice)
        # Retry only a pre-stream feature error, never a partial answer.
        for attempt in range(2):
            stream = None
            started = False
            try:
                stream = self.client.chat(
                    model=self.model, messages=messages, stream=True,
                    keep_alive=keep_alive(), options=model_options(), **options,
                )
                for chunk in stream:
                    if chunk.message.content:
                        started = True
                        yield chunk.message.content
                    # message.thinking is intentionally never yielded or stored.
                return
            except ResponseError as error:
                if (attempt == 0 and not started and options and error.status_code == 400
                        and "think" in str(error).lower()):
                    options = {}
                    notice("Thinking · runtime uses model default reasoning")
                    continue
                raise
            finally:
                if stream is not None and hasattr(stream, "close"):
                    stream.close()

    def generate(self, messages, *, reasoning=False) -> str:
        return "".join(self.stream_generate(messages, reasoning=reasoning))


_backend = None


def get_backend():
    global _backend
    if _backend is None:
        _backend = GemmaBackend()
    return _backend


def generate(messages: list[dict], *, reasoning=False) -> str:
    return get_backend().generate(messages, reasoning=reasoning)


def stream_generate(messages: list[dict], *, reasoning=False) -> Iterator[str]:
    yield from get_backend().stream_generate(messages, reasoning=reasoning)
