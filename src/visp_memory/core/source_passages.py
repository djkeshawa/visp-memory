"""Verbatim conversation passages shared by preparation and context selection."""

import re

HEADER = re.compile(
    r"(?:Session date:|Conversation date:)[^\n]*\n(?:Excerpt starts[^\n]*\n)?"
)
ROLE = re.compile(r"(?m)^(user|assistant|system):[ \t]*")
MAX_TURN_CHARS = 1600


def _line_ranges(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    """Return complete line coordinates intersecting ``start:end``."""
    ranges = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        line_end = cursor + len(line)
        if line_end > start and cursor < end:
            ranges.append((cursor, line_end, line))
        cursor = line_end
    if cursor < len(text) and cursor < end and cursor + len(text[cursor:]) > start:
        ranges.append((cursor, len(text), text[cursor:]))
    return ranges


def _heading_info(line: str) -> tuple[int, str] | None:
    """Recognize headings that establish list/section attribution.

    The line itself is evidence, so only include unambiguous structural lines.
    Ordinary prose is deliberately excluded to avoid borrowing an earlier
    section's subject when a later section happens to use the same vocabulary.
    """
    value = re.sub(r"^(?:user|assistant|system):[ \t]*", "", line.strip())
    if not value:
        return None
    indent = len(line) - len(line.lstrip())
    markdown = re.match(r"^(#{1,6})\s+", value)
    if markdown:
        return len(markdown.group(1)), "markdown"
    if re.match(r"^\d+[.)]\s+[^\n]*:\s*$", value):
        return indent + 1, "ordered"
    if re.match(r"^[-*+]\s+[^\n]*:\s*$", value):
        return indent + 1, "bullet"
    if re.match(r"^[^.!?\n]{1,120}:\s*$", value):
        return indent, "plain"
    return None


def _enclosing_heading_ranges(
    text: str, turn: tuple[int, int], start: int
) -> list[tuple[int, int]]:
    """Find structural headings directly enclosing a selected source span."""
    turn_start, turn_end = turn
    lines = [line for line in _line_ranges(text, turn_start, turn_end)
             if line[0] >= turn_start and line[1] <= turn_end]
    target_index = next(
        (index for index, (line_start, line_end, _) in enumerate(lines)
         if line_start <= start < line_end),
        None,
    )
    if target_index is None:
        return []

    # Build the heading stack in source order. This retains a top-level
    # Markdown heading across explanatory prose while replacing same-depth
    # siblings, so a selected item cannot borrow a neighboring section.
    stack: list[tuple[int, str, int, int]] = []
    for index, (line_start, line_end, line) in enumerate(lines[:target_index + 1]):
        info = _heading_info(line)
        if info is None:
            continue
        depth, kind = info
        # List indentation and Markdown heading depth are separate hierarchies:
        # an unindented list may still belong inside a Markdown section.
        while stack and (
            (kind == "markdown" and (stack[-1][1] != "markdown" or stack[-1][0] >= depth))
            or (kind != "markdown" and stack[-1][1] != "markdown" and stack[-1][0] >= depth)
        ):
            stack.pop()
        if index < target_index:
            stack.append((depth, kind, line_start, line_end))
    return [(line_start, line_end) for _depth, _kind, line_start, line_end in stack]


def turn_ranges(text: str) -> list[tuple[int, int]]:
    header = HEADER.match(text)
    start = header.end() if header else 0
    boundaries = sorted({start, *(m.start() for m in ROLE.finditer(text)), len(text)})
    return [(a, b) for a, b in zip(boundaries, boundaries[1:]) if text[a:b].strip()]


def attributed_passage(row: dict, start: int, end: int) -> dict:
    """Keep source coordinates, its clock, speaker and a bounded preceding turn."""
    text = row.get("content", "")
    start = max(0, min(len(text), start))
    end = max(start, min(len(text), end))
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
        ranges.extend(_enclosing_heading_ranges(text, (a, b), start))
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
