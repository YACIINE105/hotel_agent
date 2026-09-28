"""Streaming text helpers: strip citation markers and cut speakable segments early."""

import re

CITATION = re.compile(r"\s?\[(?:F|C):[^\]\s]{1,80}\]")
SENTENCE_END = re.compile(r"([.!?؟。…]+[\"')\]]*)(\s+)|(\n+)")
CLAUSE_END = re.compile(r"([,،;:])(\s+)")


def citations(text: str) -> set[str]:
    return {m.group(0).strip()[1:-1] for m in CITATION.finditer(text)}


class CitationFilter:
    """Removes [F:key] / [C:12] markers from a token stream without leaking fragments."""

    def __init__(self):
        self.pending = ""

    def feed(self, token: str) -> str:
        text = self.pending + token
        self.pending = ""
        start = text.rfind("[")
        if start != -1 and "]" not in text[start:] and len(text) - start < 90:
            if start > 0 and text[start - 1].isspace():
                start -= 1  # the marker regex also consumes the space before it
            text, self.pending = text[:start], text[start:]
        return CITATION.sub("", text)

    def flush(self) -> str:
        out, self.pending = CITATION.sub("", self.pending), ""
        return out


class MarkdownFilter:
    """Small models add markdown despite instructions; replies may be spoken, so strip it.

    Removes emphasis/code markers anywhere and bullet/heading markers at line starts.
    """

    def __init__(self):
        self.line_start = True

    def feed(self, text: str) -> str:
        out = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch in "*`_" and not (ch == "_" and out and out[-1].isalnum()):
                i += 1
                continue
            if self.line_start:
                if ch in " \t":
                    i += 1
                    continue
                if ch in "#-•" or (ch == "+" and text[i + 1 : i + 2] == " "):
                    i += 1
                    continue
            out.append(ch)
            self.line_start = ch == "\n"
            i += 1
        return "".join(out)


class SentenceChunker:
    """Emits complete sentences. The first segment may be cut at a clause boundary so
    speech can start sooner; later segments wait for full sentences to sound natural."""

    def __init__(self, first_clause_min: int = 40, max_len: int = 220):
        self.buf = ""
        self.first = True
        self.first_clause_min = first_clause_min
        self.max_len = max_len

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            m = SENTENCE_END.search(self.buf)
            cut = m.end() if m else None
            if cut is None and self.first:
                c = CLAUSE_END.search(self.buf, self.first_clause_min)
                cut = c.end() if c else None
            if cut is None and len(self.buf) > self.max_len:
                space = self.buf.rfind(" ", 0, self.max_len)
                cut = space + 1 if space > 0 else self.max_len
            if cut is None:
                return out
            segment, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if segment:
                out.append(segment)
                self.first = False

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


def _norm(sentence: str) -> str:
    return re.sub(r"[\W_]+", "", sentence.casefold())


class RepetitionGuard:
    """Detects degenerate loops (small quantized models can repeat one sentence until the token limit).

    `allow(sentence)` returns False for a sentence already emitted; `looping` becomes True after the
    second repeat, or when the tail of the raw text already occurred three times.
    """

    def __init__(self, max_repeats: int = 2):
        self.seen: set[str] = set()
        self.repeats = 0
        self.max_repeats = max_repeats
        self.looping = False

    def allow(self, sentence: str) -> bool:
        key = _norm(sentence)
        if not key:
            return True
        if key in self.seen:
            self.repeats += 1
            if self.repeats >= self.max_repeats:
                self.looping = True
            return False
        self.seen.add(key)
        return True

    def check_raw(self, text: str) -> None:
        if len(text) > 240:
            tail = text[-60:]
            if text.count(tail) >= 3:
                self.looping = True


def dedupe_sentences(text: str) -> str:
    """Remove repeated sentences, keeping the first occurrence and the original order."""
    chunker = SentenceChunker(first_clause_min=10**9)
    seen, out = set(), []
    for sentence in chunker.feed(text + " ") + chunker.flush():
        key = _norm(sentence)
        if key and key in seen:
            continue
        seen.add(key)
        out.append(sentence)
    return " ".join(out).strip()
