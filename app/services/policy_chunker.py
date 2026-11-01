from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PolicyChunk:
    ordinal: int
    content: str
    title_path: list[str]
    content_hash: str
    metadata: dict = field(default_factory=dict)


def content_hash(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def chunk_policy(text: str, max_chars: int = 800, overlap: int = 100) -> list[PolicyChunk]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    headings: list[str] = []
    sections: list[tuple[list[str], str]] = []
    buffer: list[str] = []

    def flush() -> None:
        value = "\n".join(item for item in buffer if item).strip()
        if value:
            sections.append((headings.copy(), value))
        buffer.clear()

    for line in lines:
        if re.match(r"^(#{1,4}\s+|[一二三四五六七八九十]+[、.]|\d+[、.])", line):
            flush()
            heading = re.sub(r"^#{1,4}\s+", "", line).strip()
            headings[:] = [heading]
        elif not line:
            flush()
        else:
            buffer.append(line)
    flush()
    if not sections and text.strip():
        sections = [([], text.strip())]

    values: list[PolicyChunk] = []
    for title_path, section in sections:
        start = 0
        while start < len(section):
            end = min(start + max_chars, len(section))
            content = section[start:end].strip()
            if content:
                values.append(
                    PolicyChunk(len(values), content, title_path, content_hash(content))
                )
            if end == len(section):
                break
            start = max(end - overlap, start + 1)
    return values
