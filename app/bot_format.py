"""Plain-text Telegram bot rendering helpers; no user-provided markup is parsed."""


def preview(body: str | None, limit: int = 90) -> str:
    clean = " ".join((body or "").split())
    return (clean[:limit] + "…") if len(clean) > limit else (clean or "(без текста)")


def chunks(text: str, length: int = 3200):
    """Split respecting Telegram's UTF-16 length (emoji may count as 2)."""
    if length <= 0:
        raise ValueError("length must be positive")
    buffer = []
    units = 0
    for ch in text:
        cost = 2 if ord(ch) > 0xFFFF else 1
        if buffer and units + cost > length:
            yield "".join(buffer)
            buffer, units = [], 0
        buffer.append(ch)
        units += cost
    if buffer:
        yield "".join(buffer)
