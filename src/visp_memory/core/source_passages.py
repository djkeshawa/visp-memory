"""Verbatim conversation passages shared by preparation and context selection."""

import re

HEADER = re.compile(
    r"(?:Session date:|Conversation date:)[^\n]*\n(?:Excerpt starts[^\n]*\n)?"
)
ROLE = re.compile(r"(?m)^(user|assistant|system):[ \t]*")
MAX_TURN_CHARS = 1600


def turn_ranges(text: str) -> list[tuple[int, int]]:
    header = HEADER.match(text)
    start = header.end() if header else 0
    boundaries = sorted({start, *(m.start() for m in ROLE.finditer(text)), len(text)})
    return [(a, b) for a, b in zip(boundaries, boundaries[1:]) if text[a:b].strip()]


def attributed_passage(row: dict, start: int, end: int) -> dict:
    """Keep source coordinates, its clock, speaker and a bounded preceding turn."""
    text = row.get("content", "")
    ranges = [(start, end)]
    header = HEADER.match(text)
    if header and header.end() <= start:
        ranges.append((0, header.end()))
    turns = turn_ranges(text)
    for index, (a, b) in enumerate(turns):
        if not a <= start < b:
            continue
        role = ROLE.match(text, a)
        if role and a < start:
            ranges.append((a, min(role.end(), start)))
        # Short preceding turns carry questions, antecedents and qualifications.
        # They remain separate verbatim source spans, never a synthesized claim.
        if index and turns[index - 1][1] - turns[index - 1][0] <= MAX_TURN_CHARS:
            ranges.append(turns[index - 1])
        break
    merged = []
    for a, b in sorted(ranges):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    spans = [{"start": a, "end": b, "text": text[a:b]} for a, b in merged]
    return {**row, "content": "\n[…]\n".join(s["text"] for s in spans), "passage_spans": spans}


def validate_source_quote(source: str, quote: str) -> dict:
    """Validate a complete single-speaker quote; context is evidence, not inference."""
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("empty source quote")
    start = source.find(quote)
    end = start + len(quote)
    if start < 0 or source.find(quote, start + 1) >= 0:
        raise ValueError("quote absent or ambiguous")
    turn = next(((a, b) for a, b in turn_ranges(source) if a <= start and end <= b), None)
    if turn is None:
        raise ValueError("quote crosses speakers or source headers")
    a, b = turn
    prefix = re.split(r"\n|(?<=[.!?])\s+", source[a:start])[-1].strip()
    suffix = source[end:b]
    if prefix not in ("", "user:", "assistant:", "system:") or (
        suffix.strip() and not suffix.startswith("\n") and not (
            quote.rstrip()[-1] in ".!?" and suffix[0].isspace()
        )
    ):
        raise ValueError("quote clips a sentence or its attribution")
    role = ROLE.match(source, a)
    speaker = role.group(1) if role else "as recorded in source"
    header = HEADER.match(source)
    if not role and header and a == header.end():
        continuation = re.search(
            r"(?m)^Excerpt starts[^\n]*; preceding speaker: (user|assistant|system)\s*$",
            header.group(),
        )
        if continuation:
            speaker = continuation.group(1)
    # Preserve complete short turns to retain the referent of words such as 'it'.
    # Long turns remain bounded; uncertain references are not silently resolved.
    left, right = (a, b) if b - a <= MAX_TURN_CHARS else (start, end)
    passage = attributed_passage({"content": source}, left, right)
    return {
        "quote": quote, "start": start, "end": end,
        "speaker": speaker,
        "context": passage["content"], "passage_spans": passage["passage_spans"],
    }
