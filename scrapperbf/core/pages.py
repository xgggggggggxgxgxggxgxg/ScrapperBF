"""Parsing Markdown *conscient des pages*.

La Layer 1 historique écrasait tout le document dans une unique « page
virtuelle ». C'est commode pour la segmentation, mais ça détruit l'information
dont un humain a besoin pour vérifier une annonce : « à quelle page du PDF
est-ce que je regarde ? ». Tous les contrôles QA de ce paquet s'appuient sur
des références de page, donc le parsing les reconstruit ici.

Deux marqueurs de fin de page sont utilisés, par ordre de fiabilité :

1. le numéro de page isolé en gras (``**34**``), posé systématiquement en pied
   de page par la conversion PDF → Markdown ;
2. le bandeau du bulletin (``N° 4466 - Vendredi 14 août 2026``), utilisé quand
   le numéro en gras manque (OCR qui perd l'emphase).

Le numéro *imprimé* est conservé séparément de l'ordinal de parsing : leur
divergence est elle-même un signal QA (page sautée, page dupliquée).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .text import normalize, preview, strip_markup

PAGE_NUMBER_FOOTER_RE = re.compile(r"^\s*\**\s*(\d{1,4})\s*\**\s*$")
BANNER_RE = re.compile(r"N°\s*\d{3,5}\s*[-–—]\s*\w+\s+\d{1,2}\s+\w+\s+\d{4}", re.IGNORECASE)
NOISE_LINE_RES = [
    re.compile(r"^www\.[\w.]+$", re.IGNORECASE),
    re.compile(r"Quotidien d.information de la Direction", re.IGNORECASE),
    re.compile(r"Marchés Publics et des Engagements Financiers", re.IGNORECASE),
    re.compile(r"^>\s*$"),
    re.compile(r"^Sommaire$", re.IGNORECASE),
]
TABLE_SEPARATOR_RE = re.compile(r"^[\s\-:|]+$")
BARE_SECTION_TITLE_RE = re.compile(
    r"^(RESULTATS\s+PROVISOIRES|APPELS?\s+D'?OFFRES|AVIS\s+DE|AVIS\s+A\s+MANIFESTATION"
    r"|PRESTATIONS\s+INTELLECTUELLES|Fournitures?\s+et\s+Services?\s+courants?|Travaux)\s*$",
    re.IGNORECASE,
)


@dataclass
class Block:
    """Unité atomique de contenu, porteuse de sa provenance."""

    type: str  # "title" | "text" | "table"
    text: str
    page_number: int
    line: int
    level: int = 0
    matrix: list[list[str]] = field(default_factory=list)

    def to_layer1(self) -> dict:
        d = {"type": self.type, "text": self.text, "page_number": self.page_number, "line": self.line}
        if self.type == "title":
            d["level"] = self.level
        if self.type == "table":
            d["matrix"] = self.matrix
        return d


@dataclass
class Page:
    ordinal: int             # rang dans le document, 1-based, toujours défini
    declared_number: int | None  # numéro imprimé en pied de page, s'il existe
    line_start: int
    line_end: int
    blocks: list[Block] = field(default_factory=list)
    raw_text: str = ""

    @property
    def page_number(self) -> int:
        """Référence de page montrée à l'humain : l'imprimée si disponible."""
        return self.declared_number if self.declared_number is not None else self.ordinal

    @property
    def is_toc(self) -> bool:
        """Page de sommaire : structurellement différente, hors périmètre métier.

        Testé sur les lignes débarrassées de leurs dièses de titre : « # Sommaire »
        est un sommaire autant que « Sommaire ».
        """
        for line in normalize(self.raw_text).splitlines():
            if re.match(r"^#*\s*sommaire\s*$", line.strip()):
                return True
        return False

    @property
    def content_chars(self) -> int:
        return sum(len(b.text) for b in self.blocks if b.type in ("text", "table"))

    def to_layer1(self) -> dict:
        return {
            "page_number": self.page_number,
            "page_idx": self.ordinal - 1,
            "declared_number": self.declared_number,
            "stream": [b.to_layer1() for b in self.blocks],
        }


@dataclass
class Document:
    pages: list[Page]
    source: str = ""

    def to_layer1(self) -> list[dict]:
        """Format attendu par ``layer2_segment.segment_document``."""
        return [p.to_layer1() for p in self.pages]

    @property
    def blocks(self) -> list[Block]:
        return [b for p in self.pages for b in p.blocks]

    def page_by_number(self, number: int) -> Page | None:
        return next((p for p in self.pages if p.page_number == number), None)


def _is_noise(line: str) -> bool:
    bare = strip_markup(line)
    if not bare:
        return True
    return any(r.search(bare) for r in NOISE_LINE_RES) or bool(BANNER_RE.search(bare))


def _parse_blocks(lines: list[tuple[int, str]], page_number: int) -> list[Block]:
    """Transforme les lignes d'une page en blocs titre / texte / tableau."""
    blocks: list[Block] = []
    text_buf: list[str] = []
    text_line = 0
    table_buf: list[list[str]] = []
    table_line = 0

    def flush_text() -> None:
        nonlocal text_buf
        if not text_buf:
            return
        joined = strip_markup(" ".join(text_buf))
        text_buf = []
        if not joined or joined.isdigit():
            return
        if BARE_SECTION_TITLE_RE.match(joined):
            # Titre de section que la conversion a perdu (pas de '#') : on le
            # réhabilite en titre, sinon la segmentation perd la frontière.
            blocks.append(Block("title", joined, page_number, text_line, level=1))
            return
        blocks.append(Block("text", joined, page_number, text_line))

    def flush_table() -> None:
        nonlocal table_buf
        if table_buf:
            flat = " | ".join(" ".join(r) for r in table_buf)
            blocks.append(Block("table", flat, page_number, table_line, matrix=table_buf))
        table_buf = []

    for lineno, raw in lines:
        stripped = raw.strip()

        if stripped.startswith("|"):
            flush_text()
            if not table_buf:
                table_line = lineno
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not TABLE_SEPARATOR_RE.match(stripped):
                table_buf.append([strip_markup(c) for c in cells])
            continue
        flush_table()

        if _is_noise(stripped):
            flush_text()
            continue

        if stripped.startswith("#"):
            flush_text()
            level = len(stripped) - len(stripped.lstrip("#"))
            title = strip_markup(stripped.lstrip("#"))
            if title:
                blocks.append(Block("title", title, page_number, lineno, level=level))
            continue

        if not stripped:
            flush_text()
            continue

        if not text_buf:
            text_line = lineno
        text_buf.append(stripped)

    flush_text()
    flush_table()
    return blocks


def parse_pages(md_text: str, source: str = "") -> Document:
    """Découpe le Markdown en pages puis en blocs, en conservant les numéros."""
    lines = md_text.splitlines()
    pages: list[Page] = []
    current: list[tuple[int, str]] = []
    start_line = 1

    def close_page(declared: int | None, end_line: int) -> None:
        nonlocal current, start_line
        ordinal = len(pages) + 1
        number = declared if declared is not None else ordinal
        page = Page(
            ordinal=ordinal,
            declared_number=declared,
            line_start=start_line,
            line_end=end_line,
            raw_text="\n".join(t for _, t in current),
        )
        page.blocks = _parse_blocks(current, number)
        pages.append(page)
        current = []
        start_line = end_line + 1

    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        m = PAGE_NUMBER_FOOTER_RE.match(stripped)
        # Un entier isolé ne ferme une page que si du contenu la précède :
        # sinon une simple énumération « 3 » en tête de page la couperait.
        if m and current and any(t.strip() for _, t in current):
            close_page(int(m.group(1)), lineno)
            continue
        current.append((lineno, raw))

    if any(t.strip() for _, t in current):
        close_page(None, len(lines))

    if not pages:
        pages = [Page(ordinal=1, declared_number=None, line_start=1, line_end=len(lines))]

    return Document(pages=pages, source=source)


def parse_file(path: str | Path) -> Document:
    p = Path(path)
    return parse_pages(p.read_text(encoding="utf-8"), source=str(p))


def describe(doc: Document) -> str:
    rows = [f"{len(doc.pages)} pages, {len(doc.blocks)} blocs — {doc.source}"]
    for page in doc.pages:
        titles = sum(1 for b in page.blocks if b.type == "title")
        tables = sum(1 for b in page.blocks if b.type == "table")
        rows.append(
            f"  p.{page.page_number:>4} (ordinal {page.ordinal}) "
            f"{titles} titres, {tables} tableaux, {page.content_chars} car. "
            f"| {preview(page.raw_text, 60)}"
        )
    return "\n".join(rows)
