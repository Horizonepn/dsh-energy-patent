#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert Chinese patent Markdown into CNIPA-style Word (.docx) files.

Usage:
    python docx.py --input spec.json
    python docx.py --input -

The spec and output contract are intentionally small and self-contained.  Only
python-docx is required at runtime.  A single JSON object is written to stdout.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Environment and imports
# ---------------------------------------------------------------------------


def _configure_stdio() -> None:
    """Force UTF-8 on standard streams (the Windows console is not UTF-8)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _remove_script_directory_from_sys_path() -> None:
    """Avoid importing this file instead of the installed ``docx`` package.

    This script is deliberately named ``docx.py``.  When executed, Python puts
    its own directory at the front of sys.path, so ``import docx`` would load
    this file recursively.  Removing only that directory lets python-docx load
    normally without affecting any other search path.
    """
    try:
        script_dir = os.path.normcase(
            os.path.abspath(os.path.dirname(os.path.abspath(__file__)))
        )
    except Exception:
        return
    kept: List[str] = []
    for entry in sys.path:
        try:
            candidate = os.path.normcase(
                os.path.abspath(entry if entry else os.getcwd())
            )
        except Exception:
            kept.append(entry)
            continue
        if candidate != script_dir:
            kept.append(entry)
    sys.path[:] = kept


def _release_own_module_name_if_imported() -> None:
    """Let a sibling ``from docx import Document`` resolve python-docx.

    This file has the requested name ``docx.py``.  If another script in this
    directory imports the installed ``docx`` package, Python would otherwise
    return this local module and fail with a circular-import error.  When we are
    imported *as* ``docx``, removing our own (still initializing) sys.modules
    entry lets the real package take the name.  CLI execution is unaffected
    because then ``__name__`` is ``__main__``.
    """
    if __name__ == "docx" and sys.modules.get("docx") is sys.modules.get(__name__):
        sys.modules.pop("docx", None)


_configure_stdio()
_remove_script_directory_from_sys_path()
_release_own_module_name_if_imported()

try:
    from docx import Document
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Mm, Pt

    _DOCX_IMPORT_ERROR: Optional[BaseException] = None
except Exception as _exc:  # pragma: no cover - exercised only when deps are absent
    Document = None  # type: ignore[assignment]
    WD_CELL_VERTICAL_ALIGNMENT = None  # type: ignore[assignment]
    WD_TABLE_ALIGNMENT = None  # type: ignore[assignment]
    WD_ALIGN_PARAGRAPH = None  # type: ignore[assignment]
    WD_BREAK = None  # type: ignore[assignment]
    OxmlElement = None  # type: ignore[assignment]
    qn = None  # type: ignore[assignment]
    Cm = Mm = Pt = None  # type: ignore[assignment]
    _DOCX_IMPORT_ERROR = _exc


BODY_FONT = "SimSun"
BODY_FONT_EA = "宋体"
HEADING_FONT = "SimHei"
HEADING_FONT_EA = "黑体"
MONO_FONT = "Consolas"

BODY_SIZE = 12.0
TITLE_SIZE = 16.0
H1_SIZE = 16.0
H2_SIZE = 14.0
H3_SIZE = 12.0
CAPTION_SIZE = 10.5
CODE_SIZE = 9.0
INLINE_CODE_SIZE = 10.5
FOOTER_SIZE = 9.0

A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0
PAGE_MARGIN_MM = 25.0
MAX_IMAGE_WIDTH_MM = 160.0
DEFAULT_IMAGE_WIDTH_MM = 150.0

FIRST_LINE_INDENT_PT = 24.0
LIST_INDENT_CM = 0.74
LIST_HANGING_CM = 0.37

# Windows reserved device names (case-insensitive, also reserved with an
# extension such as CON.docx).
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_ILLEGAL_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_LATEX_WARNING = "latex_kept_as_text"


class ConversionError(Exception):
    """An expected, user-facing conversion error."""


# ---------------------------------------------------------------------------
# Small general helpers
# ---------------------------------------------------------------------------


def _emit(payload: Dict[str, Any]) -> None:
    """Write exactly one JSON object, on one line, to stdout."""
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def _parse_cli(argv: Sequence[str]) -> str:
    """Parse only --input.  Hand-rolled so even argument errors stay JSON."""
    input_value: Optional[str] = None
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--input":
            if i + 1 >= len(argv):
                raise ConversionError("--input requires a path (or - for stdin)")
            if input_value is not None:
                raise ConversionError("--input may only be specified once")
            input_value = argv[i + 1]
            i += 2
        elif arg.startswith("--input="):
            if input_value is not None:
                raise ConversionError("--input may only be specified once")
            input_value = arg[len("--input=") :]
            i += 1
        else:
            raise ConversionError(f"unknown argument: {arg}")
    if input_value is None or input_value == "":
        raise ConversionError("--input is required")
    return input_value


def _read_spec(input_arg: str) -> Tuple[Dict[str, Any], Path]:
    if input_arg == "-":
        try:
            data = sys.stdin.buffer.read()
        except Exception:
            data = sys.stdin.read().encode("utf-8")
        base_dir = Path.cwd()
    else:
        path = Path(os.path.expanduser(input_arg))
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConversionError(f"cannot read input spec: {exc}") from exc
        base_dir = path.parent
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConversionError(f"input spec is not valid UTF-8: {exc}") from exc
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConversionError(
            f"invalid JSON in input spec at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(spec, dict):
        raise ConversionError("input spec must be a JSON object")
    return spec, base_dir


def _as_optional_text(value: Any, field: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    raise ConversionError(f"{field} must be a string")


def _bool_option(value: Any, default: bool, field: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ConversionError(f"{field} must be a boolean")


def _normalize_width_mm(value: Any) -> float:
    if value is None:
        return DEFAULT_IMAGE_WIDTH_MM
    try:
        width = float(value)
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_WIDTH_MM
    if not math.isfinite(width) or width <= 0:
        return DEFAULT_IMAGE_WIDTH_MM
    return min(width, MAX_IMAGE_WIDTH_MM)


def _sanitize_stem(raw_name: str) -> str:
    """Return a safe .docx stem from a document name."""
    name = raw_name.strip()
    if name.lower().endswith(".docx"):
        name = name[:-5].rstrip()
    name = _ILLEGAL_WINDOWS_CHARS.sub("_", name)
    name = name.strip().rstrip(" .")
    if not name:
        return "document"
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = "_" + name
    return name


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
        or ch in "“”‘’"
    )


def _join_soft_lines(lines: Sequence[str]) -> str:
    """Join Markdown soft-wrapped lines.

    Chinese line wraps should not acquire visible spaces before full-width
    punctuation.  For CJK/CJK boundaries, concatenate; otherwise use one space.
    Internal spaces are preserved.
    """
    if not lines:
        return ""
    out = lines[0].strip()
    for raw in lines[1:]:
        piece = raw.strip()
        if not piece:
            continue
        if not out:
            out = piece
        elif _is_cjk(out[-1]) and _is_cjk(piece[0]):
            out += piece
        else:
            out += " " + piece
    return out


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    j = index - 1
    while j >= 0 and text[j] == "\\":
        backslashes += 1
        j -= 1
    return backslashes % 2 == 1


def _find_unescaped(text: str, needle: str, start: int) -> int:
    j = start
    while True:
        j = text.find(needle, j)
        if j < 0:
            return -1
        if not _is_escaped(text, j):
            return j
        j += 1


def _apply_rfonts(rpr: Any, latin_font: str, east_asia_font: str) -> None:
    """Set latin/east-asian fonts directly and remove theme overrides."""
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), latin_font)
    rfonts.set(qn("w:hAnsi"), latin_font)
    rfonts.set(qn("w:eastAsia"), east_asia_font)
    rfonts.set(qn("w:cs"), latin_font)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        try:
            rfonts.attrib.pop(qn(attr), None)
        except Exception:
            pass


def _style_run(
    run: Any,
    *,
    latin_font: str,
    east_asia_font: str,
    size_pt: float,
    bold: bool = False,
    italic: bool = False,
) -> None:
    run.font.name = latin_font
    run.font.size = Pt(size_pt)
    run.bold = bool(bold)
    run.italic = bool(italic)
    _apply_rfonts(run._element.get_or_add_rPr(), latin_font, east_asia_font)


def _format_paragraph(
    paragraph: Any,
    *,
    alignment: Any,
    first_line: Any,
    left: Any = None,
    line_spacing: float = 1.5,
    space_before: float = 0.0,
    space_after: float = 0.0,
    keep_with_next: bool = False,
) -> None:
    pf = paragraph.paragraph_format
    pf.alignment = alignment
    pf.line_spacing = line_spacing
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.first_line_indent = first_line
    pf.left_indent = Cm(0) if left is None else left
    pf.right_indent = Cm(0)
    pf.keep_with_next = bool(keep_with_next)


def _set_cell_vertical_alignment(cell: Any, value: Any) -> None:
    try:
        cell.vertical_alignment = value
    except Exception:
        pass


def _set_table_borders(table: Any) -> None:
    """Apply a single, thin, black border to every edge and inner line."""
    tbl_pr = table._tbl.tblPr
    old = tbl_pr.find(qn("w:tblBorders"))
    if old is not None:
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")  # eighths of a point: 0.5 pt
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "000000")
        borders.append(element)
    # Keep tblPr children in schema order where possible.
    following = {
        qn("w:shd"),
        qn("w:tblLayout"),
        qn("w:tblCellMar"),
        qn("w:tblLook"),
        qn("w:tblCaption"),
        qn("w:tblDescription"),
    }
    insert_at = len(tbl_pr)
    for index, child in enumerate(tbl_pr):
        if child.tag in following:
            insert_at = index
            break
    tbl_pr.insert(insert_at, borders)


def _mark_header_row(row: Any) -> None:
    tr = row._tr
    tr_pr = tr.trPr
    if tr_pr is None:
        tr_pr = OxmlElement("w:trPr")
        tr.insert(0, tr_pr)
    for old in tr_pr.findall(qn("w:tblHeader")):
        tr_pr.remove(old)
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


# ---------------------------------------------------------------------------
# Markdown block parsing helpers
# ---------------------------------------------------------------------------


def _fence_opener(stripped: str) -> Optional[str]:
    match = re.match(r"^(`{3,}|~{3,})", stripped)
    return match.group(1) if match else None


def _is_fence_closer(stripped: str, opener: str) -> bool:
    char = re.escape(opener[0])
    return bool(re.fullmatch(char + "{" + str(len(opener)) + ",}", stripped))


def _is_page_break(stripped: str) -> bool:
    if stripped == "\\pagebreak":
        return True
    return bool(re.fullmatch(r"<!--\s*pagebreak\s*-->", stripped, flags=re.IGNORECASE))


def _is_horizontal_rule(stripped: str) -> bool:
    if not stripped:
        return False
    for char in ("-", "*", "_"):
        if re.fullmatch(r"(?:" + re.escape(char) + r"[ \t]*){3,}", stripped):
            return True
    return False


def _is_separator_row(cells: Sequence[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        if not re.fullmatch(r":?-{3,}:?", cell.strip()):
            return False
    return True


def _split_table_row(line: str) -> List[str]:
    """Split a pipe table row, respecting escapes and `code` spans."""
    text = line.strip()
    cells: List[str] = []
    buffer: List[str] = []
    code_ticks: Optional[int] = None
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            buffer.append(ch)
            buffer.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            j = i
            while j < len(text) and text[j] == "`":
                j += 1
            ticks = text[i:j]
            buffer.append(ticks)
            if code_ticks is None:
                code_ticks = len(ticks)
            elif code_ticks == len(ticks):
                code_ticks = None
            i = j
            continue
        if ch == "|" and code_ticks is None:
            cells.append("".join(buffer).strip())
            buffer = []
            i += 1
            continue
        buffer.append(ch)
        i += 1
    cells.append("".join(buffer).strip())

    had_leading_pipe = text.startswith("|")
    had_trailing_pipe = text.endswith("|") and not _is_escaped(text, len(text) - 1)
    if had_leading_pipe and cells and cells[0] == "":
        cells.pop(0)
    if had_trailing_pipe and cells and cells[-1] == "":
        cells.pop()
    return cells


def _parse_image_target(raw_target: str) -> str:
    """Pull a local path out of a Markdown image destination."""
    target = raw_target.strip()
    if target.startswith("<"):
        end = target.find(">")
        if end > 0:
            return target[1:end].strip()
    # Optional Markdown title after the path: ![alt](path "title")
    title_match = re.match(
        r"""^(.*?)\s+(?:"[^"]*"|'[^']*')\s*$""", target, flags=re.DOTALL
    )
    if title_match:
        target = title_match.group(1).strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    return target


def _parse_image_line(line: str) -> Optional[Tuple[str, str]]:
    """Parse an image that occupies a complete line, protecting parentheses."""
    text = line.strip()
    if not text.startswith("!["):
        return None
    alt_end = text.find("](", 2)
    if alt_end < 0:
        return None
    alt = text[2:alt_end]
    start = alt_end + 2
    i = start
    depth = 1
    in_angle = False
    escaped = False
    while i < len(text):
        ch = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\":
            escaped = True
            i += 1
            continue
        if ch == "<":
            in_angle = True
            i += 1
            continue
        if ch == ">" and in_angle:
            in_angle = False
            i += 1
            continue
        if not in_angle:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    if text[i + 1 :].strip():
                        return None
                    target = _parse_image_target(text[start:i])
                    if not target:
                        return None
                    return alt, target
        i += 1
    return None


def _ordered_list_match(stripped: str) -> Optional[Tuple[str, str, str]]:
    """Return (literal marker, source gap, content) for supported list forms."""
    # Chinese/full-width bracket forms are character-delimited and need no gap.
    nested = re.match(r"^([（(]\d+[）)])([ \t]*)(.*)$", stripped)
    if nested:
        marker, gap, content = nested.groups()
        if not content:
            return None
        return marker, gap, content

    match = re.match(r"^(\d+[.)．、])([ \t]*)(.*)$", stripped)
    if not match:
        return None
    marker, gap, content = match.groups()
    if not content:
        return None
    if not gap:
        # Do not confuse a decimal such as 1.5 with an ordered-list marker.
        if marker[-1] in ".．" and content[:1].isdigit():
            return None
    return marker, gap, content


def _unordered_list_match(stripped: str) -> Optional[Tuple[str, str]]:
    match = re.match(r"^[-*][ \t]+(.*)$", stripped)
    if not match:
        return None
    return "•", match.group(1)


# ---------------------------------------------------------------------------
# Document building
# ---------------------------------------------------------------------------


class WarningSink:
    def __init__(self) -> None:
        self.warnings: List[str] = []
        self._seen: Set[str] = set()
        self.latex_seen = False

    def add(self, message: str) -> None:
        if message not in self._seen:
            self._seen.add(message)
            self.warnings.append(message)

    def mark_latex(self) -> None:
        self.latex_seen = True


class DocumentBuilder:
    def __init__(
        self,
        *,
        cfg: Dict[str, Any],
        base_dir: Path,
        sink: WarningSink,
    ) -> None:
        self.cfg = cfg
        self.base_dir = base_dir
        self.sink = sink
        self.name = cfg["name"]
        self.title = cfg["title"]
        self.markdown = cfg["markdown"]
        self.indent_first_line = cfg["indentFirstLine"]
        self.page_numbers = cfg["pageNumbers"]
        self.meta = cfg["meta"]
        self.figures = cfg["figures"]
        self.doc = Document()

    # ---- basic building blocks ------------------------------------------

    def _warn(self, message: str) -> None:
        self.sink.add(message)

    def build(self) -> Any:
        self._configure_document()
        self._add_cover()
        self._render_markdown(self.markdown)
        self._append_figures()
        if self.page_numbers:
            self._add_page_number_footer()
        self.doc.core_properties.title = self.title.strip() or self.name
        return self.doc

    def _configure_document(self) -> None:
        section = self.doc.sections[0]
        section.page_width = Mm(A4_WIDTH_MM)
        section.page_height = Mm(A4_HEIGHT_MM)
        section.top_margin = Mm(PAGE_MARGIN_MM)
        section.bottom_margin = Mm(PAGE_MARGIN_MM)
        section.left_margin = Mm(PAGE_MARGIN_MM)
        section.right_margin = Mm(PAGE_MARGIN_MM)

        style = self.doc.styles["Normal"]
        style.font.name = BODY_FONT
        style.font.size = Pt(BODY_SIZE)
        style.font.bold = False
        _apply_rfonts(
            style.element.get_or_add_rPr(), BODY_FONT, BODY_FONT_EA
        )
        style.paragraph_format.line_spacing = 1.5
        style.paragraph_format.space_before = Pt(0)
        style.paragraph_format.space_after = Pt(0)
        style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    def _add_cover(self) -> None:
        has_title = bool(self.title.strip())
        has_meta = bool(self.meta)
        if has_title:
            p = self.doc.add_paragraph()
            _format_paragraph(
                p,
                alignment=WD_ALIGN_PARAGRAPH.CENTER,
                first_line=Pt(0),
                line_spacing=1.5,
                space_after=12,
                keep_with_next=has_meta,
            )
            run = p.add_run(self.title.strip())
            _style_run(
                run,
                latin_font=HEADING_FONT,
                east_asia_font=HEADING_FONT_EA,
                size_pt=TITLE_SIZE,
                bold=True,
            )
        if has_meta:
            self._add_meta_table()
        if has_title or has_meta:
            self._add_page_break()

    def _add_meta_table(self) -> None:
        rows = [(row["label"], row["value"]) for row in self.meta]
        table = self.doc.add_table(rows=len(rows), cols=2)
        try:
            table.style = "Table Grid"
        except Exception:
            pass
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        column_widths = (Mm(34), Mm(126))
        for index, width in enumerate(column_widths):
            table.columns[index].width = width
        for row_index, (label, value) in enumerate(rows):
            row = table.rows[row_index]
            for cell_index, width in enumerate(column_widths):
                row.cells[cell_index].width = width
                _set_cell_vertical_alignment(
                    row.cells[cell_index], WD_CELL_VERTICAL_ALIGNMENT.CENTER
                )
            label_paragraph = row.cells[0].paragraphs[0]
            _format_paragraph(
                label_paragraph,
                alignment=WD_ALIGN_PARAGRAPH.LEFT,
                first_line=Pt(0),
                line_spacing=1.0,
                space_before=2,
                space_after=2,
            )
            label_run = label_paragraph.add_run(label)
            _style_run(
                label_run,
                latin_font=BODY_FONT,
                east_asia_font=BODY_FONT_EA,
                size_pt=BODY_SIZE,
                bold=True,
            )
            value_paragraph = row.cells[1].paragraphs[0]
            _format_paragraph(
                value_paragraph,
                alignment=WD_ALIGN_PARAGRAPH.LEFT,
                first_line=Pt(0),
                line_spacing=1.0,
                space_before=2,
                space_after=2,
            )
            self._add_inline_runs(value_paragraph, value, bold=False)
        _set_table_borders(table)

    def _add_page_break(self) -> None:
        p = self.doc.add_paragraph()
        _format_paragraph(
            p,
            alignment=WD_ALIGN_PARAGRAPH.LEFT,
            first_line=Pt(0),
            line_spacing=1.0,
            space_before=0,
            space_after=0,
        )
        run = p.add_run()
        run.add_break(WD_BREAK.PAGE)

    def _add_page_number_footer(self) -> None:
        footer = self.doc.sections[0].footer
        p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        p.text = ""
        _format_paragraph(
            p,
            alignment=WD_ALIGN_PARAGRAPH.CENTER,
            first_line=Pt(0),
            line_spacing=1.0,
            space_before=0,
            space_after=0,
        )
        run = p.add_run()
        _style_run(
            run,
            latin_font=BODY_FONT,
            east_asia_font=BODY_FONT_EA,
            size_pt=FOOTER_SIZE,
        )
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instruction = OxmlElement("w:instrText")
        instruction.set(qn("xml:space"), "preserve")
        instruction.text = " PAGE "
        separate = OxmlElement("w:fldChar")
        separate.set(qn("w:fldCharType"), "separate")
        placeholder = OxmlElement("w:t")
        placeholder.text = "1"
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        run._r.append(begin)
        run._r.append(instruction)
        run._r.append(separate)
        run._r.append(placeholder)
        run._r.append(end)

    def _add_body_paragraph(self, text: str) -> None:
        if text == "":
            return
        p = self.doc.add_paragraph()
        _format_paragraph(
            p,
            alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
            first_line=(
                Pt(FIRST_LINE_INDENT_PT)
                if self.indent_first_line
                else Pt(0)
            ),
            line_spacing=1.5,
        )
        self._add_inline_runs(p, text)

    def _add_heading(self, level: int, text: str) -> None:
        size = H1_SIZE if level == 1 else H2_SIZE if level == 2 else H3_SIZE
        p = self.doc.add_paragraph()
        _format_paragraph(
            p,
            alignment=(
                WD_ALIGN_PARAGRAPH.CENTER
                if level == 1
                else WD_ALIGN_PARAGRAPH.LEFT
            ),
            first_line=Pt(0),
            line_spacing=1.5,
            space_before=12 if level == 1 else 10 if level == 2 else 8,
            space_after=6 if level == 1 else 4,
            keep_with_next=True,
        )
        run = p.add_run(text)
        _style_run(
            run,
            latin_font=HEADING_FONT,
            east_asia_font=HEADING_FONT_EA,
            size_pt=size,
            bold=True,
        )

    def _add_code_block(self, lines: Sequence[str]) -> None:
        p = self.doc.add_paragraph()
        _format_paragraph(
            p,
            alignment=WD_ALIGN_PARAGRAPH.LEFT,
            first_line=Pt(0),
            line_spacing=1.0,
            space_before=3,
            space_after=3,
        )
        run = p.add_run()
        _style_run(
            run,
            latin_font=MONO_FONT,
            east_asia_font=BODY_FONT_EA,
            size_pt=CODE_SIZE,
        )
        for index, line in enumerate(lines):
            if index:
                run.add_break(WD_BREAK.LINE)
            run.add_text(line)

    def _add_quote(self, lines: Sequence[str]) -> None:
        text = _join_soft_lines(lines)
        if not text:
            return
        p = self.doc.add_paragraph()
        _format_paragraph(
            p,
            alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
            first_line=Pt(0),
            left=Cm(LIST_INDENT_CM),
            line_spacing=1.5,
        )
        self._add_inline_runs(p, text, italic=False)

    def _add_list_item(self, marker: str, content: str, gap: str = " ") -> None:
        p = self.doc.add_paragraph()
        _format_paragraph(
            p,
            alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
            first_line=Cm(-LIST_HANGING_CM),
            left=Cm(LIST_INDENT_CM),
            line_spacing=1.5,
        )
        if marker == "•":
            marker_text = "• "
        else:
            marker_text = marker + gap
        marker_run = p.add_run(marker_text)
        _style_run(
            marker_run,
            latin_font=BODY_FONT,
            east_asia_font=BODY_FONT_EA,
            size_pt=BODY_SIZE,
        )
        self._add_inline_runs(p, content)

    def _add_image_and_caption(
        self,
        raw_path: str,
        *,
        caption: str,
        width_mm: float,
        document_name: str,
    ) -> bool:
        resolved = self._resolve_image(raw_path)
        if resolved is None:
            self._warn(f"missing_image: {raw_path} (document: {document_name})")
            return False
        p = self.doc.add_paragraph()
        _format_paragraph(
            p,
            alignment=WD_ALIGN_PARAGRAPH.CENTER,
            first_line=Pt(0),
            line_spacing=1.0,
            space_before=6,
            space_after=3,
            keep_with_next=bool(caption.strip()),
        )
        run = p.add_run()
        try:
            run.add_picture(str(resolved), width=Mm(min(width_mm, MAX_IMAGE_WIDTH_MM)))
        except Exception as exc:
            self._warn(
                f"image_error: {raw_path} ({type(exc).__name__}: {exc}) "
                f"(document: {document_name})"
            )
            # Remove the now-useless empty image paragraph.
            element = p._element
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
            return False
        if caption.strip():
            cp = self.doc.add_paragraph()
            _format_paragraph(
                cp,
                alignment=WD_ALIGN_PARAGRAPH.CENTER,
                first_line=Pt(0),
                line_spacing=1.0,
                space_before=3,
                space_after=6,
            )
            crun = cp.add_run(caption.strip())
            _style_run(
                crun,
                latin_font=BODY_FONT,
                east_asia_font=BODY_FONT_EA,
                size_pt=CAPTION_SIZE,
            )
        return True

    def _resolve_image(self, raw_path: str) -> Optional[Path]:
        if not raw_path:
            return None
        if raw_path.lower().startswith(("http://", "https://", "data:")):
            return None
        path = Path(os.path.expanduser(raw_path))
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(self.base_dir / path)
            try:
                candidates.append(Path.cwd() / path)
            except Exception:
                pass
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    # ---- inline markdown -------------------------------------------------

    def _add_inline_runs(
        self,
        paragraph: Any,
        text: str,
        *,
        size: float = BODY_SIZE,
        bold: bool = False,
        italic: bool = False,
        depth: int = 0,
    ) -> None:
        """Render **bold**, __bold__ and `code`; leave LaTeX delimiters alone."""
        if depth > 20:
            if text:
                self._append_run(
                    paragraph, text, size=size, bold=bold, italic=italic
                )
            return

        i = 0
        n = len(text)
        while i < n:
            # Inline code spans have priority over emphasis.
            if text[i] == "`":
                j = i
                while j < n and text[j] == "`":
                    j += 1
                ticks = text[i:j]
                end = text.find(ticks, j)
                if end >= 0:
                    self._append_run(
                        paragraph,
                        text[j:end],
                        size=INLINE_CODE_SIZE,
                        bold=bold,
                        italic=italic,
                        code=True,
                    )
                    i = end + len(ticks)
                    continue
                self._append_run(paragraph, "`", size=size, bold=bold, italic=italic)
                i += 1
                continue

            # \( ... \) and $ ... $ / $$ ... $$ are preserved literally.
            if text.startswith("\\(", i):
                end = text.find("\\)", i + 2)
                if end >= 0:
                    self._append_run(
                        paragraph, text[i : end + 2], size=size, bold=bold, italic=italic
                    )
                    self.sink.mark_latex()
                    i = end + 2
                    continue
                self._append_run(paragraph, "\\(", size=size, bold=bold, italic=italic)
                i += 2
                continue
            if text.startswith("$$", i) and not _is_escaped(text, i):
                end = _find_unescaped(text, "$$", i + 2)
                if end >= 0:
                    self._append_run(
                        paragraph, text[i : end + 2], size=size, bold=bold, italic=italic
                    )
                    self.sink.mark_latex()
                    i = end + 2
                    continue
                self._append_run(paragraph, "$$", size=size, bold=bold, italic=italic)
                i += 2
                continue
            if text[i] == "$" and not _is_escaped(text, i):
                end = _find_unescaped(text, "$", i + 1)
                if end >= 0:
                    self._append_run(
                        paragraph, text[i : end + 1], size=size, bold=bold, italic=italic
                    )
                    self.sink.mark_latex()
                    i = end + 1
                    continue

            # Bold/strong spans.  Parse their content recursively so `code`
            # inside bold still receives the monospace font.
            if text.startswith("**", i) or text.startswith("__", i):
                marker = text[i : i + 2]
                end = text.find(marker, i + 2)
                if end >= 0 and end > i + 2:
                    self._add_inline_runs(
                        paragraph,
                        text[i + 2 : end],
                        size=size,
                        bold=True,
                        italic=italic,
                        depth=depth + 1,
                    )
                    i = end + 2
                    continue
                self._append_run(
                    paragraph, text[i : i + 1], size=size, bold=bold, italic=italic
                )
                i += 1
                continue

            # Plain text until the next inline construct.
            j = i + 1
            while j < n:
                if text[j] in ("`", "$"):
                    break
                if text.startswith("**", j) or text.startswith("__", j):
                    break
                if text.startswith("\\(", j):
                    break
                j += 1
            self._append_run(
                paragraph, text[i:j], size=size, bold=bold, italic=italic
            )
            i = j

    def _append_run(
        self,
        paragraph: Any,
        text: str,
        *,
        size: float,
        bold: bool,
        italic: bool,
        code: bool = False,
    ) -> None:
        if text == "":
            return
        run = paragraph.add_run(text)
        _style_run(
            run,
            latin_font=MONO_FONT if code else BODY_FONT,
            east_asia_font=BODY_FONT_EA,
            size_pt=size,
            bold=bold,
            italic=italic,
        )

    # ---- tables ----------------------------------------------------------

    def _add_markdown_table(self, rows: List[List[str]]) -> None:
        if not rows:
            return
        separator_indices = {
            index for index, row in enumerate(rows) if _is_separator_row(row)
        }
        if 1 in separator_indices and len(rows) >= 2:
            header = rows[0]
            body = [
                row
                for index, row in enumerate(rows)
                if index != 0 and index not in separator_indices
            ]
        else:
            cleaned = [
                row for index, row in enumerate(rows) if index not in separator_indices
            ]
            if not cleaned:
                return
            header = cleaned[0]
            body = cleaned[1:]

        column_count = max([len(header)] + [len(row) for row in body])
        if column_count <= 0:
            self._warn("empty_table_skipped")
            return

        def padded(row: Sequence[str]) -> List[str]:
            return list(row) + [""] * (column_count - len(row))

        table = self.doc.add_table(rows=1 + len(body), cols=column_count)
        try:
            table.style = "Table Grid"
        except Exception:
            pass
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = True

        header_cells = table.rows[0].cells
        for cell, value in zip(header_cells, padded(header)):
            self._fill_table_cell(cell, value, header=True)
        _mark_header_row(table.rows[0])

        for row_index, values in enumerate(body, start=1):
            for cell, value in zip(table.rows[row_index].cells, padded(values)):
                self._fill_table_cell(cell, value, header=False)
        _set_table_borders(table)

    def _fill_table_cell(self, cell: Any, text: str, *, header: bool) -> None:
        paragraph = cell.paragraphs[0]
        _format_paragraph(
            paragraph,
            alignment=WD_ALIGN_PARAGRAPH.LEFT,
            first_line=Pt(0),
            line_spacing=1.0,
            space_before=2,
            space_after=2,
        )
        _set_cell_vertical_alignment(cell, WD_CELL_VERTICAL_ALIGNMENT.CENTER)
        # Markdown table cells commonly use \| for a literal pipe.
        value = text.replace("\\|", "|")
        self._add_inline_runs(paragraph, value, bold=header)

    # ---- markdown block parser ------------------------------------------

    def _render_markdown(self, markdown: str) -> None:
        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        pending: List[str] = []
        i = 0

        def flush_pending() -> None:
            nonlocal pending
            if pending:
                self._add_body_paragraph(_join_soft_lines(pending))
                pending = []

        while i < len(lines):
            raw = lines[i]
            line = raw.rstrip()
            stripped = line.strip()

            if not stripped:
                flush_pending()
                i += 1
                continue

            opener = _fence_opener(stripped)
            if opener is not None:
                flush_pending()
                i += 1
                code_lines: List[str] = []
                closed = False
                while i < len(lines):
                    candidate = lines[i].rstrip()
                    if _is_fence_closer(candidate.strip(), opener):
                        closed = True
                        i += 1
                        break
                    code_lines.append(candidate)
                    i += 1
                if not closed:
                    self._warn("unclosed_code_fence")
                self._add_code_block(code_lines)
                continue

            if _is_page_break(stripped):
                flush_pending()
                self._add_page_break()
                i += 1
                continue

            if stripped.startswith("<!--") and stripped.endswith("-->"):
                flush_pending()
                i += 1
                continue

            if _is_horizontal_rule(stripped):
                flush_pending()
                i += 1
                continue

            heading = re.match(r"^(#{1,3})[ \t]+(.*)$", stripped)
            if heading:
                flush_pending()
                level = len(heading.group(1))
                heading_text = heading.group(2).rstrip()
                if heading_text:
                    self._add_heading(level, heading_text)
                i += 1
                continue

            if stripped.startswith("|"):
                flush_pending()
                rows: List[List[str]] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    rows.append(_split_table_row(lines[i]))
                    i += 1
                self._add_markdown_table(rows)
                continue

            image = _parse_image_line(stripped)
            if image is not None:
                flush_pending()
                alt, target = image
                self._add_image_and_caption(
                    target,
                    caption=alt,
                    width_mm=DEFAULT_IMAGE_WIDTH_MM,
                    document_name=self.name,
                )
                i += 1
                continue

            if stripped.startswith(">"):
                flush_pending()
                quote_lines: List[str] = []
                while i < len(lines):
                    candidate = lines[i].strip()
                    if not candidate.startswith(">"):
                        break
                    quote_lines.append(re.sub(r"^>\s?", "", candidate))
                    i += 1
                self._add_quote(quote_lines)
                continue

            ordered = _ordered_list_match(stripped)
            if ordered is not None:
                flush_pending()
                marker, gap, content = ordered
                self._add_list_item(marker, content, gap)
                i += 1
                continue

            unordered = _unordered_list_match(stripped)
            if unordered is not None:
                flush_pending()
                marker, content = unordered
                self._add_list_item(marker, content)
                i += 1
                continue

            pending.append(line)
            i += 1

        flush_pending()

    # ---- figures array ---------------------------------------------------

    def _append_figures(self) -> None:
        for figure in self.figures:
            raw_path = figure["path"]
            caption = figure["caption"]
            width_mm = _normalize_width_mm(figure.get("widthMm"))
            self._add_image_and_caption(
                raw_path,
                caption=caption,
                width_mm=width_mm,
                document_name=self.name,
            )


# ---------------------------------------------------------------------------
# Spec normalization and conversion
# ---------------------------------------------------------------------------


def _normalize_documents(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_documents = spec.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise ConversionError("spec.documents must be a non-empty array")

    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_documents):
        prefix = f"documents[{index}]"
        if not isinstance(raw, dict):
            raise ConversionError(f"{prefix} must be an object")
        raw_name = raw.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ConversionError(f"{prefix}.name must be a non-empty string")
        title = _as_optional_text(raw.get("title"), f"{prefix}.title")
        markdown = _as_optional_text(raw.get("markdown"), f"{prefix}.markdown")
        indent = _bool_option(
            raw.get("indentFirstLine"), True, f"{prefix}.indentFirstLine"
        )
        page_numbers = _bool_option(
            raw.get("pageNumbers"), True, f"{prefix}.pageNumbers"
        )

        raw_meta = raw.get("meta", [])
        if raw_meta is None:
            raw_meta = []
        if not isinstance(raw_meta, list):
            raise ConversionError(f"{prefix}.meta must be an array")
        meta: List[Dict[str, str]] = []
        for meta_index, item in enumerate(raw_meta):
            if not isinstance(item, dict):
                raise ConversionError(
                    f"{prefix}.meta[{meta_index}] must be an object"
                )
            meta.append(
                {
                    "label": _as_optional_text(
                        item.get("label"), f"{prefix}.meta[{meta_index}].label"
                    ).strip(),
                    "value": _as_optional_text(
                        item.get("value"), f"{prefix}.meta[{meta_index}].value"
                    ).strip(),
                }
            )

        raw_figures = raw.get("figures", [])
        if raw_figures is None:
            raw_figures = []
        if not isinstance(raw_figures, list):
            raise ConversionError(f"{prefix}.figures must be an array")
        figures: List[Dict[str, Any]] = []
        for figure_index, item in enumerate(raw_figures):
            if not isinstance(item, dict):
                raise ConversionError(
                    f"{prefix}.figures[{figure_index}] must be an object"
                )
            raw_path = item.get("path", "")
            if raw_path is None:
                raw_path = ""
            if not isinstance(raw_path, str):
                raise ConversionError(
                    f"{prefix}.figures[{figure_index}].path must be a string"
                )
            figures.append(
                {
                    "path": raw_path,
                    "caption": _as_optional_text(
                        item.get("caption"),
                        f"{prefix}.figures[{figure_index}].caption",
                    ).strip(),
                    "widthMm": item.get("widthMm"),
                }
            )

        normalized.append(
            {
                "name": raw_name.strip(),
                "title": title,
                "markdown": markdown,
                "indentFirstLine": indent,
                "pageNumbers": page_numbers,
                "meta": meta,
                "figures": figures,
            }
        )
    return normalized


def _resolve_output_dir(spec: Dict[str, Any], base_dir: Path) -> Path:
    raw_out = spec.get("outDir")
    if not isinstance(raw_out, str) or not raw_out.strip():
        raise ConversionError("spec.outDir must be a non-empty string")
    expanded = os.path.expanduser(raw_out.strip())
    out_dir = Path(expanded)
    if not out_dir.is_absolute():
        out_dir = base_dir / out_dir
    out_dir = Path(os.path.abspath(str(out_dir)))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConversionError(f"cannot create outDir '{out_dir}': {exc}") from exc
    if not out_dir.is_dir():
        raise ConversionError(f"outDir is not a directory: {out_dir}")
    return out_dir


def convert(spec: Dict[str, Any], base_dir: Path) -> Dict[str, Any]:
    if _DOCX_IMPORT_ERROR is not None:
        raise ConversionError(
            "python-docx is not available in this Python environment: "
            f"{_DOCX_IMPORT_ERROR}"
        )

    documents = _normalize_documents(spec)
    out_dir = _resolve_output_dir(spec, base_dir)
    sink = WarningSink()
    files: List[Dict[str, Any]] = []
    used_filenames: Set[str] = set()

    for cfg in documents:
        stem = _sanitize_stem(cfg["name"])
        candidate_stem = stem
        suffix = 2
        while os.path.normcase(candidate_stem + ".docx") in used_filenames:
            candidate_stem = f"{stem} ({suffix})"
            suffix += 1
        used_filenames.add(os.path.normcase(candidate_stem + ".docx"))
        out_path = out_dir / (candidate_stem + ".docx")

        builder = DocumentBuilder(cfg=cfg, base_dir=base_dir, sink=sink)
        doc = builder.build()
        try:
            doc.save(str(out_path))
        except OSError as exc:
            raise ConversionError(f"cannot save '{out_path}': {exc}") from exc

        files.append(
            {
                "name": candidate_stem,
                "path": str(out_path),
                "paragraphs": len(doc.paragraphs),
                "images": len(doc.inline_shapes),
            }
        )

    if sink.latex_seen:
        sink.add(_LATEX_WARNING)

    return {"ok": True, "files": files, "warnings": sink.warnings}


def _error_text(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return message
    return type(exc).__name__


def main(argv: Sequence[str]) -> int:
    try:
        input_arg = _parse_cli(argv)
        spec, base_dir = _read_spec(input_arg)
        payload = convert(spec, base_dir)
    except ConversionError as exc:
        _emit({"ok": False, "error": _error_text(exc)})
        return 1
    except Exception as exc:  # Keep the CLI contract even on unexpected errors.
        _emit({"ok": False, "error": _error_text(exc)})
        return 1
    _emit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
