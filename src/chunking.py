"""Stage 2: turn each cleaned document into retrievable chunks.

The chunking strategy is decided by the shape of the real corpus, not by a library default
(PRD §9.3). Two observations drive the whole module. First, a fact page is mostly *short
labelled blocks* - "Min. for SIP" on one line and "Rs 100" on the next - not long prose, so the
label/value pair is the atomic unit and fixed-size windows would shred it. Second, fee and load
data lives in compact tables, and an overlapped table row produces a chunk that reads as a
different fee, so overlap is applied to prose sections only (architecture.md §8.3).

Token counts use the embedding model's own tokenizer rather than a character heuristic, because
`min_tokens`/`max_tokens` are only meaningful in the unit the encoder will actually see
(architecture.md §8.2).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from dataclasses import dataclass, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from huggingface_hub import hf_hub_download

from src.config import Settings, configure_console, load_settings
from src.loading import load_all
from src.models import ChunkRecord, LoadedDoc, SectionType
from src.registry import Registry, load_registry

SPECIAL_TOKEN_ALLOWANCE = 2
FALLBACK_CHARS_PER_TOKEN = 4
DEFAULT_FALLBACK_MAX_TOKENS = 256
BOILERPLATE_MIN_TOKENS = 25
LABEL_MAX_WORDS = 5
MIN_CHUNK_TOKENS = 4
VALUE_MAX_CHARS = 60
DEFINITION_MAX_WORDS = 14
FALLBACK_HEADING = "Overview"

# A labelled block on the corpus pages is rendered as two lines - the label, then the value -
# so this shape is a definition unit and not two paragraphs to be glued back together later.
TABLE_LINE = re.compile(r"^\|")
HEADING_LINE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
LIST_LINE = re.compile(r"^-\s+")
SENTENCE_END = re.compile(r"[.!?]$")
DIGIT = re.compile(r"\d")
PROPER_NOUN_VALUE = re.compile(r"[A-Za-z][\w&./\-]*(?:\s+[A-Z][\w&./\-]*){0,2}")

BOILERPLATE_PHRASES: tuple[str, ...] = (
    "disclaimer",
    "mutual fund investments are subject to market risks",
    "read more",
    "know more",
    "download app",
    "log in",
    "login",
    "sign up",
    "click here",
    "terms and conditions",
    "privacy policy",
    "cookie",
    "all rights reserved",
    "follow us",
    "download on the app store",
)
# Headings that are chrome wherever they appear, so the body is dropped even when a promo phrase
# was reformatted away: the phrase list is a substring test and the page's wording varies.
BOILERPLATE_HEADINGS: frozenset[str] = frozenset(
    {
        "disclaimer",
        "disclosures",
        "terms and conditions",
        "privacy policy",
        "cookie policy",
        "about us",
        "contact us",
        "our offices",
        "need help?",
        "frequently asked questions",
        "faq",
        "references",
        "appendix",
    }
)

# Checked in this order, so the most specific family wins a heading that mentions several.
# "tax" and "lock" are checked before "fees" because an ELSS tax section reads like a fee
# section to a keyword matcher, and mislabelling it would let overlap leak into it.
SECTION_KEYWORDS: tuple[tuple[SectionType, tuple[str, ...]], ...] = (
    (
        SectionType.TAX,
        ("statement", "tax", "capital gain", "tax report", "download", "how to", "stamp duty"),
    ),
    (
        SectionType.LOCK_IN,
        ("lock", "80c", "tax saver", "elss", "holding period"),
    ),
    (
        SectionType.RISK,
        ("riskometer", "benchmark", "risk", "objective", "horizon", "category", "portfolio"),
    ),
    (
        SectionType.FEES,
        (
            "expense",
            "ratio",
            "fee",
            "charge",
            "load",
            "ter",
            "aum",
            "minimum",
            "min.",
            "sip",
            "amount",
            "nav",
        ),
    ),
    (SectionType.GENERAL, ()),
)


class Variant(str, Enum):
    """The chunking variants compared by ablation A1 (implementation.md Phase 4).

    The number in each name is the *requested* bound; the bound actually used is
    `min(requested, the encoder's ceiling)`. With `all-MiniLM-L6-v2` that ceiling is 254, so
    `semantic_350`, `semantic_600` and `fixed_512` all run at 254 and `semantic_150` is what makes
    the comparison a real one. The names stay as implementation.md specifies them; the collapse is
    recorded in architecture.md §8.6 rather than hidden by renaming them.
    """

    SEMANTIC_150 = "semantic_150"
    SEMANTIC_350 = "semantic_350"
    SEMANTIC_600 = "semantic_600"
    FIXED_512 = "fixed_512"

    @property
    def bounds(self) -> tuple[int, int]:
        """Return the (max_tokens, overlap_tokens) this variant requests."""
        return {Variant.SEMANTIC_150: (150, 60), Variant.SEMANTIC_350: (350, 60),
                Variant.SEMANTIC_600: (600, 60), Variant.FIXED_512: (512, 50)}[self]


class UnitKind(str, Enum):
    """What a unit is made of, which is what decides whether it may be overlapped."""

    TABLE = "table"
    DEFINITION = "definition"
    LIST = "list"
    PARAGRAPH = "paragraph"


class Unit(NamedTuple):
    """One indivisible piece of a section: a table block, a labelled fact, a list, or prose."""

    kind: UnitKind
    text: str
    header_row: str = ""


class Section(NamedTuple):
    """A heading and the text beneath it, as produced by the loader's markdown-ish output."""

    heading: str
    body: str
    ordinal: int


class DraftChunk(NamedTuple):
    """A candidate chunk before ordinals and ids are assigned, so merging can renumber."""

    heading: str
    section_type: SectionType
    text: str
    merged_headings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenCounter:
    """Token counting in the encoder's own unit, with a warned-about character fallback."""

    tokenizer: object | None
    max_tokens: int
    used_fallback: bool

    def count(self, text: str) -> int:
        """Return the number of tokens text occupies for the embedding model.

        Longer-than-model text is counted, never truncated, because `max_tokens` must describe
        what the encoder would actually receive. `verbose=False` silences the tokenizer's own
        "sequence length is longer" notice, which is expected here and would print once per
        oversized section.
        """
        if self.tokenizer is None:
            return len(text) // FALLBACK_CHARS_PER_TOKEN
        return len(self.tokenizer(text, add_special_tokens=False, verbose=False)["input_ids"])


@lru_cache(maxsize=1)
def get_tokenizer(model_id: str, cache_dir: str) -> object | None:
    """Return the embedding model's tokenizer, or None once if it cannot be downloaded."""
    from transformers import AutoTokenizer

    try:
        return AutoTokenizer.from_pretrained(model_id, cache_dir=cache_dir)
    except Exception as exc:  # noqa: BLE001 - any failure must degrade, not crash the build
        print(
            f"warning: could not load the tokenizer for {model_id} ({type(exc).__name__}: {exc}); "
            f"falling back to {FALLBACK_CHARS_PER_TOKEN} characters per token. Token bounds become "
            "estimates, not the encoder's real units (architecture.md §8.2).",
            file=sys.stderr,
        )
        return None


def model_token_ceiling(model_id: str) -> int | None:
    """Return the model's own `max_seq_length`, or None when it cannot be read offline.

    `all-MiniLM-L6-v2` advertises a 512-token tokenizer but its sentence-transformers config sets
    `max_seq_length` to 256, so the encoder drops everything past 256. A 510-token chunk is then
    not a big chunk, it is a 254-token chunk with a discarded tail, and the stored text and the
    embedded text are not the same text. The tokenizer cannot tell us this, so the model's config
    is the authority, and reading one small JSON file beats loading the model to ask.
    """
    try:
        path = hf_hub_download(repo_id=model_id, filename="sentence_bert_config.json")
        limit = json.loads(Path(path).read_text(encoding="utf-8")).get("max_seq_length")
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return int(limit) if isinstance(limit, int) and limit > 0 else None


def make_token_counter(settings: Settings) -> TokenCounter:
    """Return a counter whose max_tokens cannot exceed what the encoder will actually accept."""
    tokenizer = get_tokenizer(settings.embedding.model_id, settings.paths.resolve("model_cache_dir").as_posix())
    declared = settings.chunking.max_tokens
    if tokenizer is None:
        return TokenCounter(tokenizer, declared, True)
    encoder_max = min(
        int(getattr(tokenizer, "model_max_length", DEFAULT_FALLBACK_MAX_TOKENS)),
        DEFAULT_FALLBACK_MAX_TOKENS * 2,
    ) - SPECIAL_TOKEN_ALLOWANCE
    declared_limit = model_token_ceiling(settings.embedding.model_id)
    if declared_limit is not None:
        encoder_max = min(encoder_max, declared_limit - SPECIAL_TOKEN_ALLOWANCE)
    if declared > encoder_max:
        print(
            f"warning: chunking.max_tokens is {declared} but the encoder accepts {encoder_max} "
            f"tokens ({declared_limit or 512} minus {SPECIAL_TOKEN_ALLOWANCE} special tokens). "
            "Clamping, because a longer chunk is silently truncated by the encoder and its tail "
            "never reaches the vector (architecture.md §8.2).",
            file=sys.stderr,
        )
    return TokenCounter(tokenizer, min(declared, encoder_max), False)


def parse_sections(text: str) -> list[Section]:
    """Split cleaned text into sections on markdown headings, giving leading prose a heading.

    Pages begin with a stat panel before any heading, so the text before the first heading would
    otherwise be dropped or need a null heading; "Overview" keeps it retrievable and named.
    """
    sections: list[Section] = []
    heading = FALLBACK_HEADING
    body: list[str] = []
    for line in text.split("\n"):
        match = HEADING_LINE.match(line)
        if match:
            if any(part.strip() for part in body):
                sections.append(Section(heading, "\n".join(body).strip(), len(sections)))
            heading = match.group(2).strip()
            body = []
            continue
        body.append(line)
    if any(part.strip() for part in body):
        sections.append(Section(heading, "\n".join(body).strip(), len(sections)))
    return sections


def classify_section(heading: str, body: str) -> SectionType:
    """Return the fact family of a section from its heading, falling back to its body.

    A heading is checked first and the body only when the heading is vague, because headings are
    written by the publisher and are far more reliable than keyword density in the body - a
    benchmark line inside a fees section would otherwise relabel it.
    """
    heading_text = heading.lower()
    for section_type, keywords in SECTION_KEYWORDS:
        if any(keyword in heading_text for keyword in keywords):
            return section_type
    body_text = body.lower()
    for section_type, keywords in SECTION_KEYWORDS:
        if any(keyword in body_text for keyword in keywords):
            return section_type
    return SectionType.GENERAL


def _is_label(line: str) -> bool:
    """Return True for a short, unpunctuated line that can head a labelled fact.

    Five words is the limit because a label is a noun phrase, not a clause. At eight words,
    "Mr. Dhruv has done B.Com, CA and CFA" was read as the label of the following line, which
    left "Education" as a chunk holding one word and pushed the qualification into a different
    chunk from the qualification label it belongs to.
    """
    stripped = line.strip()
    if not stripped or TABLE_LINE.match(stripped) or LIST_LINE.match(stripped):
        return False
    if HEADING_LINE.match(stripped) or SENTENCE_END.search(stripped):
        return False
    if len(stripped.split()) > LABEL_MAX_WORDS:
        return False
    return bool(stripped)


def _is_value(line: str) -> bool:
    """Return True for a short line that reads as the value of the label above it."""
    stripped = line.strip()
    if not _is_label(stripped):
        return False
    if len(stripped) > VALUE_MAX_CHARS:
        return False
    if DIGIT.search(stripped):
        return True
    return bool(PROPER_NOUN_VALUE.fullmatch(stripped))


def split_into_units(section: Section) -> list[Unit]:
    """Split one section into table blocks, labelled facts, lists, and paragraph runs.

    Blank lines are dropped before splitting. The loader separates every block element with a
    blank line, so a rule that treats a blank line as a boundary turns one 50-row holdings table
    into 50 one-row tables and separates every "Min. for SIP" from its "Rs 100"; neither is a
    retrievable unit. Keeping the splitter tolerant of blank-line style means it does not depend
    on that convention.

    A table is a single unit and is never split mid-row here; `chunk_document` is what decides
    how to group rows when one table alone exceeds max_tokens, because only it knows which chunks
    have already been flushed.
    """
    lines = [line.rstrip() for line in section.body.split("\n") if line.strip()]
    units: list[Unit] = []
    table: list[str] = []
    bullets: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush_table() -> None:
        if table:
            header_row = table[0]
            units.append(Unit(UnitKind.TABLE, "\n".join(table), header_row))
            table.clear()

    def flush_bullets() -> None:
        if bullets:
            units.append(Unit(UnitKind.LIST, "\n".join(bullets)))
            bullets.clear()

    def flush_paragraph() -> None:
        if paragraph:
            units.append(Unit(UnitKind.PARAGRAPH, "\n".join(paragraph)))
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if TABLE_LINE.match(line):
            flush_bullets()
            flush_paragraph()
            table.append(line)
            index += 1
            continue
        flush_table()
        if LIST_LINE.match(line):
            flush_paragraph()
            bullets.append(line)
            index += 1
            continue
        flush_bullets()
        if _is_label(line) and index + 1 < len(lines) and _is_value(lines[index + 1]):
            flush_paragraph()
            paragraph.append(line)
            paragraph.append(lines[index + 1].strip())
            index += 1
        else:
            paragraph.append(line)
        index += 1
    flush_table()
    flush_bullets()
    flush_paragraph()
    return units


def drop_boilerplate(section: Section, section_type: SectionType, counter: TokenCounter) -> bool:
    """Return True when a section is chrome or too thin to retrieve and should be dropped.

    A section is dropped for one of three reasons and no others: it repeats a known disclaimer or
    promo phrase, its heading is chrome, or it is too short to carry a fact. The thinness test
    demands both a low token count and no digits, because "Rs 100" or "62.5%" is a fact even in
    three words, while a short paragraph with no number rarely answers a question on its own.
    Fees and lock-in are never dropped for thinness: "Exit load of 1%" is five words and one of
    the most-asked questions about a fund.
    """
    lowered = section.body.lower()
    if any(phrase in lowered for phrase in BOILERPLATE_PHRASES):
        return True
    if section.heading.strip().lower() in BOILERPLATE_HEADINGS:
        return True
    if section_type in {SectionType.FEES, SectionType.LOCK_IN}:
        return False
    if counter.count(section.body) < BOILERPLATE_MIN_TOKENS and not DIGIT.search(section.body):
        return True
    return False


def _table_groups(units: list[Unit], max_tokens: int, counter: TokenCounter) -> list[Unit]:
    """Split oversized table units into row groups, repeating the header row in each group."""
    out: list[Unit] = []
    for unit in units:
        if unit.kind is not UnitKind.TABLE or counter.count(unit.text) <= max_tokens:
            out.append(unit)
            continue
        rows = unit.text.split("\n")
        header, body_rows = rows[0], [row for row in rows[1:] if row.strip()]
        group: list[str] = []
        for row in body_rows:
            candidate = "\n".join([header, *group, row])
            if group and counter.count(candidate) > max_tokens:
                out.append(Unit(UnitKind.TABLE, "\n".join([header, *group]), header))
                group = []
            group.append(row)
        if group:
            out.append(Unit(UnitKind.TABLE, "\n".join([header, *group]), header))
    return out


ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "sh",
        "b",
        "m",
        "ph",
        "ca",
        "cfa",
        "cma",
        "cs",
        "llb",
        "bcom",
        "msc",
        "ltd",
        "inc",
        "pvt",
        "vs",
        "viz",
        "no",
        "approx",
    }
)


def _split_sentences(text: str) -> list[str]:
    """Split text at sentence boundaries, leaving abbreviations whole.

    Splitting naively at every period cuts "Mr. Dhruv has done B.Com" into "Mr." / "Dhruv has
    done B." / "Com", which rewrites the manager's qualification as two fragments in the stored
    text. An abbreviation is a real word boundary that is not a sentence, so the word in front of
    the period decides.
    """
    out: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]+\s+", text):
        head = text[start : match.start()]
        tail_word = re.findall(r"[A-Za-z]+$", head)
        if tail_word and tail_word[0].lower() in ABBREVIATIONS:
            continue
        sentence = text[start : match.end()].strip()
        if sentence:
            out.append(sentence)
        start = match.end()
    remainder = text[start:].strip()
    if remainder:
        out.append(remainder)
    return out or [text.strip()]


def _split_oversized_unit(unit: Unit, max_tokens: int, counter: TokenCounter) -> list[Unit]:
    """Split one unit that alone exceeds max_tokens into units that each fit inside it.

    Sentences are the unit of splitting, not words: a boundary inside a sentence can cut away the
    fact the sentence states, while a boundary between sentences cannot. The split is lossless -
    the earlier version stopped packing once the budget was reached and silently discarded the
    rest of the paragraph, which on S1 threw away six of thirteen sentences of the fund's mandate.
    Word splitting is the last resort, for a single sentence longer than the whole budget.
    """
    if counter.count(unit.text) <= max_tokens:
        return [unit]
    pieces = [Unit(unit.kind, piece) for piece in _split_sentences(unit.text) if piece]
    if len(pieces) <= 1:
        return _word_units(unit, max_tokens, counter)
    out: list[Unit] = []
    for piece in pieces:
        out.extend(_split_oversized_unit(piece, max_tokens, counter))
    return out


def _word_units(unit: Unit, max_tokens: int, counter: TokenCounter) -> list[Unit]:
    """Split one unsplittable unit on word boundaries, keeping the text in order."""
    words = unit.text.split()
    out: list[Unit] = []
    group: list[str] = []
    for word in words:
        if group and counter.count(" ".join([*group, word])) > max_tokens:
            out.append(Unit(unit.kind, " ".join(group)))
            group = [word]
            continue
        group.append(word)
    if group:
        out.append(Unit(unit.kind, " ".join(group)))
    return out or [unit]


def _overlappable(section_type: SectionType) -> bool:
    """Return True when overlap is allowed for this section type.

    Tables and labelled fee lists are excluded on purpose: repeating a fee row inside the next
    chunk makes the chunk read as a different fee, which is the specific failure PRD §9.3 warns
    about. Tax is excluded for the same reason - a repeated "taxed at 20%" line attached to a
    different redemption period is a wrong answer, not redundant context.
    """
    return section_type in {SectionType.GENERAL, SectionType.RISK}


def _tail_for_overlap(
    units: list[Unit], section_type: SectionType, overlap_tokens: int, counter: TokenCounter
) -> list[Unit]:
    """Return the trailing units covering overlap_tokens, or nothing for a non-prose section."""
    if overlap_tokens <= 0 or not _overlappable(section_type):
        return []
    tail: list[Unit] = []
    running = 0
    for unit in reversed(units):
        size = counter.count(unit.text)
        if running + size > overlap_tokens and tail:
            break
        tail.insert(0, unit)
        running += size
    return tail


def _render(units: list[Unit]) -> str:
    return "\n".join(unit.text for unit in units).strip()


def _seed_overlap(
    body_units: list[Unit],
    size: int,
    section_type: SectionType,
    overlap_tokens: int,
    max_tokens: int,
    counter: TokenCounter,
) -> list[Unit]:
    """Return the overlap tail to seed the next chunk with, trimmed so the next unit still fits.

    The trim drops the oldest tail unit first, because the tail exists to carry the sentence
    immediately before the boundary, and the sentence before that is the first to go. Without the
    trim, seeding a 254-token unit with a 41-token tail and then adding the unit produced a
    295-token chunk, and a 361-token chunk followed it - the bound was checked before the flush
    and never checked again.
    """
    tail = _tail_for_overlap(body_units, section_type, overlap_tokens, counter)
    while tail and sum(counter.count(item.text) for item in tail) + size > max_tokens:
        tail = tail[1:]
    return tail


def _section_chunks(
    section: Section, section_type: SectionType, max_tokens: int, overlap_tokens: int, counter: TokenCounter
) -> tuple[list[DraftChunk], int]:
    """Run the flush/overlap loop for one section and return its drafts and fragment count.

    A section that yields a single draft is kept whole however short it is, because it *is* the
    section: S3's exit load is the two words "Exit load" and "Nil", which together answer the
    question completely. A draft below `MIN_CHUNK_TOKENS` is only dropped when the section
    produced others, where it is the residue of a mis-paired label - "Education" left standing
    alone after its value was paired with the line below it - and such a chunk costs a slot in the
    top-k budget to say nothing.
    """
    units = _table_groups(split_into_units(section), max_tokens, counter)
    units = [piece for unit in units for piece in _split_oversized_unit(unit, max_tokens, counter)]
    drafts: list[DraftChunk] = []
    body_units: list[Unit] = []
    body_tokens = 0
    for unit in units:
        size = counter.count(unit.text)
        if body_units and body_tokens + size > max_tokens:
            drafts.append(
                DraftChunk(section.heading, section_type, _render(body_units), (section.heading,))
            )
            body_units = _seed_overlap(
                body_units, size, section_type, overlap_tokens, max_tokens, counter
            )
            body_tokens = sum(counter.count(item.text) for item in body_units)
        body_units.append(unit)
        body_tokens += size
    if body_units:
        drafts.append(DraftChunk(section.heading, section_type, _render(body_units), (section.heading,)))
    if len(drafts) < 2:
        return drafts, 0
    kept = [draft for draft in drafts if counter.count(draft.text) >= MIN_CHUNK_TOKENS]
    return kept, len(drafts) - len(kept)


def _has_table_rows(text: str) -> bool:
    return any(TABLE_LINE.match(line) for line in text.split("\n"))


def merge_small_sections(
    drafts: list[DraftChunk], min_tokens: int, max_tokens: int, counter: TokenCounter
) -> tuple[list[DraftChunk], int]:
    """Absorb chunks below min_tokens into a sibling of the same section type, and count merges.

    Three refusals, each because the merged chunk would answer a different question than its
    parts. (1) Different `SectionType`: on S1 a 12-token "Exit load" chunk, a 4-token "Stamp duty"
    chunk and a 48-token "Tax implication" chunk sit next to a 94-token fund-manager bio, and a
    purely positional merge packs the manager into a tax chunk. (2) A table on exactly one side:
    appending "Min. for SIP: Rs 100" to the tail of a 350-token holdings table produces a chunk
    that spends most of its budget on 50 stock names and answers the fee question from its last
    line. (3) Over `max_tokens`, because a truncated fee block looks complete and is not.

    A small chunk that survives all three is left on its own, which is fine: it is still
    retrievable, and both headings are kept, joined with " / ", when a merge does happen.
    """
    if not drafts:
        return drafts, 0
    merged: list[DraftChunk] = []
    merges = 0
    for draft in drafts:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.section_type is draft.section_type
            and counter.count(draft.text) < min_tokens
            and _has_table_rows(previous.text) is _has_table_rows(draft.text)
        ):
            combined = f"{previous.text}\n\n{draft.text}"
            if counter.count(combined) <= max_tokens:
                headings = tuple(dict.fromkeys((*previous.merged_headings, draft.heading)))
                merged[-1] = DraftChunk(" / ".join(headings), previous.section_type, combined, headings)
                merges += 1
                continue
        merged.append(draft)
    return merged, merges


def _dedupe_by_hash(drafts: list[DraftChunk]) -> tuple[list[DraftChunk], int]:
    """Drop drafts whose text repeats, which a nav block split across sections produces."""
    seen: set[str] = set()
    out: list[DraftChunk] = []
    for draft in drafts:
        digest = hashlib.sha256(draft.text.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out.append(draft)
    return out, len(drafts) - len(out)


def _finalise(doc: LoadedDoc, drafts: list[DraftChunk], counter: TokenCounter) -> list[ChunkRecord]:
    """Assign ordinals, chunk ids, headers and embed text to the surviving drafts."""
    records: list[ChunkRecord] = []
    for ordinal, draft in enumerate(drafts):
        heading = draft.heading
        header = f"[{doc.source.scheme_name}] {heading}"
        digest = hashlib.sha1(
            f"{doc.source.source_id}|{heading}|{ordinal}".encode("utf-8")
        ).hexdigest()[:16]
        records.append(
            ChunkRecord(
                chunk_id=digest,
                source_id=doc.source.source_id,
                scheme_id=doc.source.scheme_id,
                scheme_name=doc.source.scheme_name,
                section=heading,
                section_type=draft.section_type,
                text=draft.text,
                embed_text=f"{header}\n{draft.text}",
                url=doc.source.url,
                title=doc.source.title,
                fetched_at=doc.source.fetched_at,
                ordinal=ordinal,
                token_count=counter.count(draft.text),
            )
        )
    return records


def effective_bounds(
    variant: Variant | None, settings: Settings, counter: TokenCounter
) -> tuple[int, int]:
    """Return (max_tokens, overlap_tokens) for this run, clamped to what the encoder accepts.

    `config.yaml` is the source of truth; a `Variant` is an explicit override used by ablation
    A1 and never a default. The result is then clamped to the encoder's real ceiling, because a
    `max_tokens` above it is not a larger chunk but a chunk whose tail is silently dropped before
    it becomes a vector, so the stored text and the embedded text would not be the same text.
    """
    if variant is None:
        max_tokens, overlap_tokens = settings.chunking.max_tokens, settings.chunking.overlap_tokens
    else:
        max_tokens, overlap_tokens = variant.bounds
    ceiling = counter.max_tokens
    return min(max_tokens, ceiling), min(overlap_tokens, max(0, ceiling - 1))


def chunk_document(
    doc: LoadedDoc,
    settings: Settings,
    counter: TokenCounter,
    variant: Variant | None = None,
) -> list[ChunkRecord]:
    """Return every retrievable chunk of one document (architecture.md §8.3)."""
    return chunk_document_with_stats(doc, settings, counter, variant)[0]


def chunk_document_with_stats(
    doc: LoadedDoc,
    settings: Settings,
    counter: TokenCounter,
    variant: Variant | None = None,
) -> tuple[list[ChunkRecord], dict[str, int]]:
    """Return the chunks plus the section-level counts ablation A1 reports.

    `dropped` and `merged` are reported because a chunk count alone hides a misfire: a corpus can
    reach a healthy median while the chunker has silently deleted the tax sections as boilerplate
    or shredded every fee table into unusable fragments.
    """
    max_tokens, overlap_tokens = effective_bounds(variant, settings, counter)
    if variant is Variant.FIXED_512:
        records = _chunk_fixed(doc, max_tokens, overlap_tokens, counter)
        return records, {
            "dropped_sections": 0,
            "merged_chunks": 0,
            "deduped_chunks": 0,
            "dropped_fragments": 0,
        }
    drafts: list[DraftChunk] = []
    dropped = 0
    fragments = 0
    for section in parse_sections(doc.text):
        section_type = classify_section(section.heading, section.body)
        if settings.chunking.drop_boilerplate and drop_boilerplate(section, section_type, counter):
            dropped += 1
            continue
        section_drafts, section_fragments = _section_chunks(
            section, section_type, max_tokens, overlap_tokens, counter
        )
        drafts.extend(section_drafts)
        fragments += section_fragments
    unique, deduped = _dedupe_by_hash(drafts)
    merges = 0
    if settings.chunking.merge_small_sections:
        unique, merges = merge_small_sections(
            unique, settings.chunking.min_tokens, max_tokens, counter
        )
    records = _finalise(doc, unique, counter)
    if not settings.chunking.include_context_header:
        records = [replace_record_header(record) for record in records]
    return records, {
        "dropped_sections": dropped,
        "merged_chunks": merges,
        "deduped_chunks": deduped,
        "dropped_fragments": fragments,
    }


def replace_record_header(record: ChunkRecord) -> ChunkRecord:
    """Return the record with its context header removed from the embedded text."""
    return replace(record, embed_text=record.text)


def _chunk_fixed(doc: LoadedDoc, max_tokens: int, overlap_tokens: int, counter: TokenCounter) -> list[ChunkRecord]:
    """Chunk by fixed token windows: the rejected baseline kept for ablation A1."""
    units = [Unit(UnitKind.PARAGRAPH, part) for part in doc.text.split("\n\n") if part.strip()]
    units = [piece for unit in units for piece in _split_oversized_unit(unit, max_tokens, counter)]
    drafts: list[DraftChunk] = []
    body_units: list[Unit] = []
    for unit in units:
        if body_units and counter.count(_render([*body_units, unit])) > max_tokens:
            drafts.append(
                DraftChunk(
                    FALLBACK_HEADING, SectionType.GENERAL, _render(body_units), (FALLBACK_HEADING,)
                )
            )
            body_units = _seed_overlap(
                body_units,
                counter.count(unit.text),
                SectionType.GENERAL,
                overlap_tokens,
                max_tokens,
                counter,
            )
        body_units.append(unit)
    if body_units:
        drafts.append(
            DraftChunk(FALLBACK_HEADING, SectionType.GENERAL, _render(body_units), (FALLBACK_HEADING,))
        )
    return _finalise(doc, drafts, counter)


def chunk_stats(chunks: list[ChunkRecord]) -> dict[str, object]:
    """Return the count, section mix and token distribution ablation A1 compares variants on."""
    counts = sorted(chunk.token_count for chunk in chunks)
    by_section: dict[str, int] = {}
    for chunk in chunks:
        key = chunk.section_type.value
        by_section[key] = by_section.get(key, 0) + 1
    stats: dict[str, object] = {
        "count": len(chunks),
        "by_section_type": dict(sorted(by_section.items())),
    }
    if not counts:
        return stats | {"median_tokens": 0, "p10_tokens": 0, "p90_tokens": 0, "max_tokens": 0}
    return stats | {
        "median_tokens": int(statistics.median(counts)),
        "p10_tokens": counts[max(0, int(len(counts) * 0.1) - 1)],
        "p90_tokens": counts[min(len(counts) - 1, int(len(counts) * 0.9))],
        "max_tokens": counts[-1],
    }


def write_chunks_jsonl(chunks: list[ChunkRecord], path: Path) -> Path:
    """Write every chunk as one JSON object per line, for inspection and the eval harness."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(
                json.dumps(
                    {
                        "chunk_id": chunk.chunk_id,
                        "source_id": chunk.source_id,
                        "scheme_id": chunk.scheme_id,
                        "scheme_name": chunk.scheme_name,
                        "section": chunk.section,
                        "section_type": chunk.section_type.value,
                        "text": chunk.text,
                        "embed_text": chunk.embed_text,
                        "url": chunk.url,
                        "title": chunk.title,
                        "fetched_at": chunk.fetched_at,
                        "ordinal": chunk.ordinal,
                        "token_count": chunk.token_count,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return path


def chunk_all(
    registry: Registry, settings: Settings, variant: Variant | None = None
) -> tuple[list[ChunkRecord], list[str]]:
    """Chunk every loaded source and return the chunks plus one warning per source that failed."""
    counter = make_token_counter(settings)
    docs, load_warnings = load_all(registry, settings)
    chunks: list[ChunkRecord] = []
    for doc in docs:
        chunks.extend(chunk_document(doc, settings, counter, variant))
    return chunks, list(load_warnings)


def main(argv: list[str] | None = None) -> int:
    """Chunk one source or the whole corpus and print the sections, units, chunks and stats."""
    configure_console()
    parser = argparse.ArgumentParser(
        prog="python -m src.chunking", description="Stage 2: chunk the cleaned corpus."
    )
    parser.add_argument("--doc", help="source_id to trace, e.g. S1; omit to chunk everything")
    parser.add_argument(
        "--variant",
        choices=[item.value for item in Variant],
        default=None,
        help="override max_tokens and overlap in memory only, for ablation A1",
    )
    parser.add_argument("--dump", action="store_true", help="write data/chunks.jsonl")
    args = parser.parse_args(argv)

    settings = load_settings()
    registry = load_registry(settings)
    variant = Variant(args.variant) if args.variant else None
    counter = make_token_counter(settings)
    max_tokens, overlap_tokens = effective_bounds(variant, settings, counter)
    docs, load_warnings = load_all(registry, settings)
    for warning in load_warnings:
        print(f"warning: {warning}")
    if args.doc:
        docs = [doc for doc in docs if doc.source.source_id == args.doc]
        if not docs:
            print(f"no loaded source with id {args.doc!r}", file=sys.stderr)
            return 1

    all_chunks: list[ChunkRecord] = []
    plan_stats: dict[str, int] = {}
    for doc in docs:
        sections = parse_sections(doc.text)
        if args.doc:
            print(f"\n=== {doc.source.source_id} {doc.source.scheme_name} ===")
            for section in sections:
                section_type = classify_section(section.heading, section.body)
                units = split_into_units(section)
                print(
                    f"\n[{section.ordinal}] {section.heading!r} -> {section_type.value} "
                    f"({counter.count(section.body)} tokens, {len(units)} units)"
                )
                for unit in units:
                    preview = unit.text.replace("\n", " / ")
                    print(f"    {unit.kind.value:<11} {counter.count(unit.text):>4}  {preview[:88]}")
        chunks, doc_stats = chunk_document_with_stats(doc, settings, counter, variant)
        all_chunks.extend(chunks)
        for key, value in doc_stats.items():
            plan_stats[key] = plan_stats.get(key, 0) + value
        if args.doc:
            print(f"\n--- {len(chunks)} chunks ---")
            for chunk in chunks:
                overlap = _overlappable(chunk.section_type)
                print(
                    f"  {chunk.chunk_id}  ord={chunk.ordinal:<3} {chunk.section_type.value:<8} "
                    f"{chunk.token_count:>4}tok  overlap={'yes' if overlap else 'no ':<3} "
                    f"{chunk.section[:52]}"
                )

    stats = chunk_stats(all_chunks)
    label = f"variant {variant.value}" if variant else f"config strategy {settings.chunking.strategy!r}"
    print(f"\n{label}  max_tokens={max_tokens} overlap={overlap_tokens}")
    print(f"count={stats['count']}  median_tokens={stats['median_tokens']}  "
          f"p10={stats['p10_tokens']}  p90={stats['p90_tokens']}  max={stats['max_tokens']}")
    print(f"by_section_type={stats['by_section_type']}")
    print(f"dropped_sections={plan_stats.get('dropped_sections', 0)}  "
          f"merged_chunks={plan_stats.get('merged_chunks', 0)}  "
          f"deduped_chunks={plan_stats.get('deduped_chunks', 0)}  "
          f"dropped_fragments={plan_stats.get('dropped_fragments', 0)}")
    if stats["max_tokens"] and int(stats["max_tokens"]) > max_tokens:
        print(f"ERROR: a chunk exceeds max_tokens {max_tokens}", file=sys.stderr)
        return 1
    if args.dump:
        path = write_chunks_jsonl(all_chunks, settings.paths.resolve("chunks_dump"))
        print(f"wrote {stats['count']} chunks to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
