import os
from collections.abc import Iterator

from ollama import Client

MODEL = os.getenv("MOTE_MODEL", "gemma4:e2b")

client = Client(
    host=os.getenv("MOTE_OLLAMA_HOST", "http://localhost:11434"),
    timeout=120,
)


def generate(messages: list[dict]) -> str:
    """Return a complete response from Gemma."""
    response = client.chat(
        model=MODEL,
        messages=messages,
    )
    return response.message.content or ""


def stream_generate(messages: list[dict]) -> Iterator[str]:
    """Stream response chunks as Gemma generates them."""
    stream = client.chat(
        model=MODEL,
        messages=messages,
        stream=True,
    )

    for chunk in stream:
        if chunk.message.content:
            yield chunk.message.content


if __name__ == "__main__":
    messages = [
        {"role": "user", "content": "Explain recursion in one sentence."}
    ]

    for chunk in stream_generate(messages):
        print(chunk, end="", flush=True)

    print()

