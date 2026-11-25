"""Texts prepared for retrieval: each chunk stands on its own.

An item (a ticket, a report, a commit ...) becomes one or more chunks. Every chunk has a header that
says what it is (kind and reference, project, date, status, labels, title), so a hit is understood
without its neighbours and a search for "the decision about X" finds decisions. The text is cleaned of
markdown and other noise (links keep their words, code keeps its identifiers), and long texts are cut
at paragraph boundaries with an overlap, small enough for the model's 512 tokens (``CHUNK_CHARS``). The hash of a chunk
says whether it changed: an unchanged one is never embedded again.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

# Measured with the models' tokenizer (XLM-R) on real tickets: 2.6 to 3.4 characters a token. 1000
# characters with the header are about 200 to 400 tokens, inside the 512 the e5 models read; small enough
# that a hit is about one thing. A rarer denser text (code, paths) is not cut off: the embedder splits
# what is longer than its model reads into windows (embedding.LocalOnnxEmbedder). The overlap carries
# the end of a chunk into the next one.
CHUNK_CHARS = 1000
OVERLAP_CHARS = 150
MAX_CHUNKS = 50  # ~50 000 characters: only a pasted log or dump is longer; of it the first part (the limit keeps one item from flooding the index)
MIN_CHARS = 12  # shorter is noise ("ok", "+1")

KIND_NAMES = {"ticket": "Ticket", "report": "Report", "decision": "Decision", "comment": "Comment", "note": "Note", "commit": "Commit", "idea": "Idea", "doc": "Doc page", "file": "File"}


@dataclass
class Doc:
    """One item of a project's history, as an indexer reads it."""

    kind: str
    source_id: str
    ref: str  # how an agent names it: #64, assignment #5043, note 12, 1a2b3c4d5e
    title: str
    body: str
    at: datetime
    facts: list[str] = field(default_factory=list)  # for the header: status, labels, author ...
    ticket_number: int | None = None
    repo: str | None = None
    changed: datetime | None = None  # when it last changed at its source; None: when it was made
    paths: list[str] | None = None  # a commit's changed files
    links: dict[str, int | None] = field(default_factory=dict)  # what it comes from (SearchDocument.task_id ...): deleted with it
    max_chunks: int = MAX_CHUNKS
    sections: bool = False  # markdown: cut at its headings, each chunk names its section


@dataclass
class Chunk:
    chunk: int
    header: str
    body: str
    hash: str

    @property
    def text(self) -> str:
        return f"{self.header}\n{self.body}"


_FENCE = re.compile(r"^\s*(```|~~~)[\w+-]*\s*$", re.M)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)(]|\([^)]*\))*\)")
_HTML = re.compile(r"</?[a-zA-Z][^>\n]{0,200}>")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_QUOTE = re.compile(r"^\s{0,3}>\s?", re.M)
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)(?=\S)(.+?)(?<=\S)\1")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$", re.M)
_URL = re.compile(r"https?://\S{60,}")
_SPACES = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def clean(text: str) -> str:
    """Markdown and noise out, words in: what a person would read."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _FENCE.sub("", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HTML.sub(" ", text)
    text = _TABLE_RULE.sub("", text)
    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _URL.sub(lambda m: m.group(0)[:60] + "…", text)
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def _pieces(text: str, size: int) -> list[str]:
    """Paragraphs, each at most ``size``: a longer one is cut at sentence ends, then at words."""
    out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            out.append(paragraph)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?:;])\s+|\n", paragraph):
            while len(sentence) > size:  # one endless line (a log, a list of paths)
                cut = sentence.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                if current:
                    out.append(current)
                    current = ""
                out.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if current and len(current) + 1 + len(sentence) > size:
                out.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
        if current:
            out.append(current)
    return out


def split(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS, max_chunks: int = MAX_CHUNKS) -> list[str]:
    """Chunks of at most about ``size`` characters, cut between paragraphs; each after the first
    starts with the last paragraph(s) of the one before, up to ``overlap`` characters."""
    pieces = _pieces(text, size)
    chunks: list[list[str]] = []
    current: list[str] = []
    length = 0
    for piece in pieces:
        if current and length + len(piece) > size:
            chunks.append(current)
            carried: list[str] = []
            for previous in reversed(current):
                if sum(len(p) for p in carried) + len(previous) > overlap:
                    break
                carried.insert(0, previous)
            current, length = carried, sum(len(p) + 2 for p in carried)
        current.append(piece)
        length += len(piece) + 2
    if current:
        chunks.append(current)
    return ["\n\n".join(c) for c in chunks[:max_chunks]]


_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_LINE = re.compile(r"^\s*(```|~~~)")


def sections(text: str) -> list[tuple[str, str]]:
    """A markdown text by its headings: (the path of headings above it, "A › B", its text). Headings in code
    blocks are none."""
    out: list[tuple[str, str]] = []
    trail: list[str] = []
    lines: list[str] = []
    fenced = False
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if _FENCE_LINE.match(line):
            fenced = not fenced
        heading = None if fenced else _ATX.match(line)
        if heading is None:
            lines.append(line)
            continue
        out.append((" › ".join(trail), "\n".join(lines)))
        level = len(heading.group(1))
        trail = [*trail[: level - 1], clean(heading.group(2)).replace("\n", " ")[:120]]
        lines = []
    out.append((" › ".join(trail), "\n".join(lines)))
    return [(path, body) for path, body in out if path or body.strip()]


def split_sections(text: str, size: int = CHUNK_CHARS, max_chunks: int = MAX_CHUNKS) -> list[str]:
    """Chunks of a markdown text that follow its headings: short sections share a chunk, a long one is cut
    (with overlap) and each of its chunks starts with its heading path."""
    chunks: list[str] = []
    current = ""
    for path, raw in sections(text):
        body = clean(raw)
        whole = f"{path}\n{body}".strip() if path else body
        if not whole:
            continue
        if current and len(current) + 2 + len(whole) <= size:
            current = f"{current}\n\n{whole}"
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(whole) <= size:
            current = whole
            continue
        room = size - len(path) - 1 if path else size
        parts = split(body, max(room, size // 2), max_chunks=max_chunks - len(chunks))
        chunks += [f"{path}\n{p}" if path else p for p in parts]
        if len(chunks) >= max_chunks:
            break
    if current:
        chunks.append(current)
    return chunks[:max_chunks]


def header(doc: Doc, project: str, part: int = 0, parts: int = 1) -> str:
    kind = KIND_NAMES.get(doc.kind, doc.kind.title())
    line = " · ".join([f"{kind} {doc.ref}".strip(), project, doc.at.strftime("%Y-%m-%d"), *[f for f in doc.facts if f]])
    if parts > 1:
        line += f" · part {part + 1} of {parts}"
    title = clean(doc.title).replace("\n", " ")
    return f"{line}\n{title}" if title else line


def content_hash(header_text: str, body: str) -> str:
    return hashlib.sha256(f"{header_text}\n{body}".encode()).hexdigest()


def prepare(doc: Doc, project: str) -> list[Chunk]:
    """The chunks of an item; none when there is nothing worth finding."""
    body = clean(doc.body)
    if len(body) < MIN_CHARS and len(clean(doc.title)) < MIN_CHARS:
        return []
    texts = (split_sections(doc.body, max_chunks=doc.max_chunks) if doc.sections else split(body, max_chunks=doc.max_chunks)) or [""]
    chunks = []
    for i, text in enumerate(texts):
        head = header(doc, project, i, len(texts))
        chunks.append(Chunk(i, head, text, content_hash(head, text)))
    return chunks
