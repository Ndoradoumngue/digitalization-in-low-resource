"""A small text-only PDF writer for generated reports (GED Phase 6: the
destruction report, "procès-verbal d'élimination").

No layout engine is shipped with the API (pypdf reads and stamps PDFs but
can't lay out text), so this writes the PDF objects directly: A4 pages,
the standard Helvetica / Helvetica-Bold fonts (WinAnsi - characters outside
it print as "?", like the download watermark), word-wrapped paragraphs and
simple tables, page numbers in the footer.

    doc = Report(footer="PV ELIM-...")
    doc.heading("Procès-verbal d'élimination")
    doc.paragraph("...")
    doc.table(["Cote", "Type"], [["A-1", "facture"]], widths=[0.3, 0.7])
    data = doc.build()
"""

from __future__ import annotations

PAGE_W, PAGE_H = 595.28, 841.89  # A4 in points
MARGIN = 50.0
_BODY = 9.5
_LEADING = 1.35

# Helvetica advance widths (per 1000 em) for WinAnsi 32..255 - enough to
# wrap lines; unknown characters count as an average glyph.
_HELV_WIDTHS = {
    " ": 278,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    ":": 278,
    ";": 278,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "I": 278,
    "J": 500,
    "M": 833,
    "W": 944,
    "f": 278,
    "i": 222,
    "j": 222,
    "l": 222,
    "m": 833,
    "r": 333,
    "t": 278,
    "w": 722,
    "[": 278,
    "]": 278,
    "_": 556,
    "|": 260,
}


def _char_width(ch: str, bold: bool) -> float:
    if ch.isdigit():
        w = 556
    elif ch in _HELV_WIDTHS:
        w = _HELV_WIDTHS[ch]
    elif ch.isupper():
        w = 667
    else:
        w = 556
    return w * (1.05 if bold else 1.0)


def text_width(s: str, size: float, bold: bool = False) -> float:
    return sum(_char_width(c, bold) for c in s) * size / 1000


def wrap(s: str, width: float, size: float, bold: bool = False) -> list[str]:
    """Greedy word wrap; words longer than a line are cut."""
    lines: list[str] = []
    for para in str(s).split("\n"):
        line = ""
        for word in para.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, size, bold) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            while text_width(word, size, bold) > width and len(word) > 1:
                cut = len(word)
                while cut > 1 and text_width(word[:cut], size, bold) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            line = word
        lines.append(line)
    return lines


def _pdf_str(s: str) -> bytes:
    raw = s.encode("cp1252", "replace")
    return (
        b"("
        + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        + b")"
    )


class Report:
    def __init__(self, footer: str = ""):
        self.footer = footer
        self.pages: list[list[bytes]] = []
        self._new_page()

    # ── layout primitives ────────────────────────────────────────────────

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def _need(self, height: float) -> None:
        if self.y - height < MARGIN + 20:
            self._new_page()

    def _text(
        self, x: float, y: float, s: str, size: float, bold: bool = False
    ) -> None:
        font = b"/F2" if bold else b"/F1"
        self.pages[-1].append(
            b"BT %s %.2f Tf %.2f %.2f Td %s Tj ET\n" % (font, size, x, y, _pdf_str(s))
        )

    def _line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.pages[-1].append(
            b"0.6 G 0.5 w %.2f %.2f m %.2f %.2f l S\n" % (x1, y1, x2, y2)
        )

    # ── content ──────────────────────────────────────────────────────────

    def heading(self, s: str, size: float = 15) -> None:
        width = PAGE_W - 2 * MARGIN
        lines = wrap(s, width, size, bold=True)
        self._need(len(lines) * size * _LEADING + 8)
        for line in lines:
            self.y -= size * _LEADING
            self._text(MARGIN, self.y, line, size, bold=True)
        self.y -= 6

    def subheading(self, s: str) -> None:
        self.y -= 4
        self.heading(s, size=11.5)

    def paragraph(self, s: str, size: float = _BODY, bold: bool = False) -> None:
        width = PAGE_W - 2 * MARGIN
        for line in wrap(s, width, size, bold):
            self._need(size * _LEADING)
            self.y -= size * _LEADING
            self._text(MARGIN, self.y, line, size, bold)
        self.y -= 4

    def fields(self, pairs: list[tuple[str, str]], size: float = _BODY) -> None:
        """Label / value lines (labels bold, values wrapped beside them)."""
        label_w = max((text_width(k, size, True) for k, _ in pairs), default=0) + 10
        width = PAGE_W - 2 * MARGIN - label_w
        for label, value in pairs:
            lines = wrap(value or "-", width, size)
            self._need(len(lines) * size * _LEADING)
            for i, line in enumerate(lines):
                self.y -= size * _LEADING
                if i == 0:
                    self._text(MARGIN, self.y, label, size, bold=True)
                self._text(MARGIN + label_w, self.y, line, size)
        self.y -= 4

    def table(
        self,
        headers: list[str],
        rows: list[list[str]],
        widths: list[float],
        size: float = 7.5,
    ) -> None:
        total = PAGE_W - 2 * MARGIN
        cols = [w * total for w in widths]
        pad = 3

        def draw_row(cells: list[str], bold: bool) -> None:
            wrapped = [
                wrap(c or "", w - 2 * pad, size, bold) for c, w in zip(cells, cols)
            ]
            height = max(len(w) for w in wrapped) * size * _LEADING + 2 * pad
            if self.y - height < MARGIN + 20:
                self._new_page()
                if not bold:
                    draw_row(headers, True)
            top = self.y
            x = MARGIN
            for lines, w in zip(wrapped, cols):
                y = top - pad
                for line in lines:
                    y -= size * _LEADING
                    self._text(x + pad, y + 2, line, size, bold)
                x += w
            self.y = top - height
            self._line(MARGIN, self.y, MARGIN + total, self.y)

        self._line(MARGIN, self.y, MARGIN + total, self.y)
        draw_row(headers, True)
        for row in rows:
            draw_row(row, False)
        self.y -= 8

    def space(self, points: float = 8) -> None:
        self.y -= points

    # ── output ───────────────────────────────────────────────────────────

    def build(self) -> bytes:
        n = len(self.pages)
        objects: list[bytes] = []

        def add(obj: bytes) -> int:
            objects.append(obj)
            return len(objects)

        font1 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        )
        font2 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
        )
        pages_id = len(objects) + 1 + 2 * n  # reserved after the page objects
        page_ids = []
        for i, ops in enumerate(self.pages, 1):
            footer = f"{self.footer}    {i} / {n}" if self.footer else f"{i} / {n}"
            ops = ops + [
                b"BT /F1 7 Tf %.2f %.2f Td %s Tj ET\n"
                % (MARGIN, MARGIN - 20, _pdf_str(footer))
            ]
            stream = b"".join(ops)
            content = add(
                b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
            )
            page_ids.append(
                add(
                    b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f]"
                    b" /Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>"
                    % (pages_id, PAGE_W, PAGE_H, font1, font2, content)
                )
            )
        kids = b" ".join(b"%d 0 R" % p for p in page_ids)
        assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n)) == pages_id
        catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, obj in enumerate(objects, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1,
            catalog,
            xref,
        )
        return bytes(out)
