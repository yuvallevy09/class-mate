from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VttCue:
    start_sec: float
    end_sec: float
    text: str


_TS_RE = re.compile(
    r"^\s*(?P<s>\d{2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})\s*-->\s*(?P<e>\d{2}:\d{2}:\d{2}\.\d{3}|\d{1,2}:\d{2}\.\d{3})"
)


def _parse_timestamp(ts: str) -> float:
    ts = (ts or "").strip()
    if not ts:
        raise ValueError("empty timestamp")

    # WebVTT supports either HH:MM:SS.mmm or MM:SS.mmm.
    if ts.count(":") == 2:
        hh, mm, rest = ts.split(":")
        ss, ms = rest.split(".")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
    if ts.count(":") == 1:
        mm, rest = ts.split(":")
        ss, ms = rest.split(".")
        return int(mm) * 60 + int(ss) + int(ms) / 1000.0
    raise ValueError(f"invalid timestamp: {ts}")


def parse_webvtt(text: str) -> list[VttCue]:
    """
    Parse a WebVTT captions file into cues.

    MVP parser:
    - Ignores NOTE/STYLE/REGION blocks (treated as non-cues).
    - Ignores cue identifiers.
    - Extracts timing line + subsequent text lines until a blank line.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    cues: list[VttCue] = []
    i = 0

    # Skip header
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().upper().startswith("WEBVTT"):
        i += 1

    def skip_block() -> None:
        nonlocal i
        # Skip until blank line.
        while i < len(lines) and lines[i].strip():
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Skip NOTE/STYLE/REGION blocks.
        upper = line.upper()
        if upper.startswith("NOTE") or upper.startswith("STYLE") or upper.startswith("REGION"):
            i += 1
            skip_block()
            continue

        # Cue identifier line can appear before the timestamp line.
        # If next line is a timestamp, treat this line as an identifier and skip it.
        if i + 1 < len(lines) and _TS_RE.match(lines[i + 1].strip()):
            i += 1
            line = lines[i].strip()

        m = _TS_RE.match(line)
        if not m:
            # Not a cue; skip block-ish content defensively.
            i += 1
            continue

        start = _parse_timestamp(m.group("s"))
        end = _parse_timestamp(m.group("e"))
        i += 1

        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1

        # Consume blank separator(s)
        while i < len(lines) and not lines[i].strip():
            i += 1

        cue_text = " ".join(t for t in text_lines if t).strip()
        if cue_text:
            cues.append(VttCue(start_sec=float(start), end_sec=float(end), text=cue_text))

    # Ensure cues are in time order
    cues.sort(key=lambda c: (c.start_sec, c.end_sec))
    return cues


def merge_cues(
    cues: list[VttCue],
    *,
    max_chars: int = 700,
    max_window_sec: float = 30.0,
) -> list[VttCue]:
    """
    Merge small consecutive cues into embedding-friendly chunks.
    Even though we aren't embedding yet, storing merged segments keeps the DB from exploding.
    """
    out: list[VttCue] = []
    buf: list[VttCue] = []

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        start = buf[0].start_sec
        end = buf[-1].end_sec
        text = " ".join(c.text for c in buf).strip()
        if text:
            out.append(VttCue(start_sec=float(start), end_sec=float(end), text=text))
        buf = []

    for cue in cues:
        if not buf:
            buf.append(cue)
            continue

        candidate_text = (" ".join([c.text for c in buf] + [cue.text])).strip()
        candidate_start = buf[0].start_sec
        candidate_end = cue.end_sec
        candidate_window = float(candidate_end - candidate_start)

        if len(candidate_text) <= int(max_chars) and candidate_window <= float(max_window_sec):
            buf.append(cue)
        else:
            flush()
            buf.append(cue)

    flush()
    return out


