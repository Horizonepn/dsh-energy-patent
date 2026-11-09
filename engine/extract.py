#!/usr/bin/env python
"""Extract readable text and structure from patent source materials.

Reads one JSON spec from --input (or stdin) and prints one JSON object.  The
spec is deliberately small so the host plugin can call this on Windows without
extra dependencies:

  {"action": "file", "path": "E:/case/方案.docx", "maxChars": 20000}
  {"action": "dir",  "path": "E:/case", "maxFiles": 40}
  {"action": "text", "path": "E:/case/note.eml"}

Supported: txt/md/tex/csv/json/yaml/py and other plain text, docx (python-docx),
pptx (python-pptx), xlsx (openpyxl), pdf (pdftotext), eml (stdlib email).
Images are listed but not OCR'd.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".tex", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".py", ".m", ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".js", ".ts", ".jsx",
    ".tsx", ".log", ".xml", ".html", ".htm", ".ini", ".cfg", ".toml", ".mermaid",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg"}
SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "dist", "build", "out", "outputs",
    "__pycache__", ".venv", "venv", ".idea", ".vscode", ".dsh", "vendor",
}
PRIORITY = {".docx": 0, ".pdf": 1, ".md": 2, ".txt": 3, ".tex": 4, ".pptx": 5, ".xlsx": 6}


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _plain_text(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    title = ""
    headings: list[str] = []
    if path.suffix.lower() in {".md", ".markdown"}:
        for line in raw.splitlines():
            m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
            if m:
                headings.append(m.group(2).strip())
                if not title and len(m.group(1)) == 1:
                    title = m.group(2).strip()
    return {"text": raw, "title": title, "headings": headings[:40]}


def _docx_text(path: Path) -> dict[str, Any]:
    from docx import Document  # type: ignore

    doc = Document(str(path))
    parts: list[str] = []
    headings: list[str] = []
    for para in doc.paragraphs:
        txt = para.text.strip()
        if not txt:
            continue
        style = (para.style.name or "") if para.style is not None else ""
        if style.lower().startswith("heading") or style.startswith("标题"):
            headings.append(txt)
        parts.append(txt)
    for table in doc.tables:
        rows: list[str] = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append("[表格]\n" + "\n".join(rows))
    images: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            images = [
                n for n in zf.namelist()
                if n.startswith("word/media/") and not n.endswith("/")
            ]
    except Exception:
        pass
    title = headings[0] if headings else ""
    return {"text": "\n\n".join(parts), "title": title, "headings": headings[:40], "images": images}


def _pptx_text(path: Path) -> dict[str, Any]:
    from pptx import Presentation  # type: ignore

    prs = Presentation(str(path))
    parts: list[str] = []
    headings: list[str] = []
    for idx, slide in enumerate(prs.slides, 1):
        block: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                txt = "".join(run.text for run in para.runs).strip()
                if txt:
                    block.append(txt)
        if block:
            headings.append(block[0])
            parts.append(f"[幻灯片 {idx}]\n" + "\n".join(block))
    return {"text": "\n\n".join(parts), "title": headings[0] if headings else "", "headings": headings[:40]}


def _xlsx_text(path: Path) -> dict[str, Any]:
    from openpyxl import load_workbook  # type: ignore

    wb = load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            parts.append(f"[工作表 {ws.title}]")
            count = 0
            for row in ws.iter_rows(values_only=True):
                values = ["" if v is None else str(v) for v in row]
                if any(values):
                    parts.append(" | ".join(values).rstrip(" |"))
                    count += 1
                if count >= 200:
                    parts.append("…（行数已截断）")
                    break
    finally:
        wb.close()
    return {"text": "\n".join(parts), "title": path.stem, "headings": []}


def _find_pdftotext() -> str | None:
    for name in ("pdftotext.exe", "pdftotext"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        r"E:\texlive\2026\bin\windows\pdftotext.exe",
        r"C:\Program Files\poppler\Library\bin\pdftotext.exe",
        r"C:\Program Files\poppler\bin\pdftotext.exe",
    ]
    for cand in candidates:
        if Path(cand).is_file():
            return cand
    return None


def _pdf_text(path: Path) -> dict[str, Any]:
    exe = _find_pdftotext()
    if not exe:
        return {
            "text": "",
            "title": path.stem,
            "headings": [],
            "warnings": ["pdftotext_not_found：请安装 Poppler/pdftotext，或把 PDF 另存为 DOCX/文本"],
        }
    proc = subprocess.run(
        [exe, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        timeout=120,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    warnings: list[str] = []
    if proc.returncode != 0:
        warnings.append(f"pdftotext_exit_{proc.returncode}")
    if len(re.sub(r"\s", "", text)) < 40:
        warnings.append("extracted_text_too_short：可能是扫描件或没有文字层，请换可复制文字的 PDF 或先 OCR")
    return {"text": text, "title": path.stem, "headings": [], "warnings": warnings}


def _eml_text(path: Path) -> dict[str, Any]:
    msg = BytesParser(policy=policy.default).parse(path.open("rb"))
    body_parts: list[str] = []
    attachments: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        filename = part.get_filename()
        if filename:
            attachments.append(filename)
            continue
        if ctype in {"text/plain", "text/html"}:
            try:
                content = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/html":
                content = _html_to_text(str(content))
            body_parts.append(str(content))
    subject = str(msg.get("Subject") or "")
    return {
        "text": "\n\n".join(body_parts),
        "title": subject,
        "headings": [],
        "attachments": attachments,
        "messageId": str(msg.get("Message-ID") or ""),
        "from": str(msg.get("From") or ""),
        "to": str(msg.get("To") or ""),
        "date": str(msg.get("Date") or ""),
    }


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return html.unescape(raw)


def extract_one(path: Path, max_chars: int) -> dict[str, Any]:
    suffix = path.suffix.lower()
    item: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "kind": suffix.lstrip(".") or "unknown",
        "chars": 0,
        "truncated": False,
        "text": "",
        "title": "",
        "headings": [],
        "images": [],
        "warnings": [],
    }
    if suffix in IMAGE_EXTS:
        try:
            item["bytes"] = path.stat().st_size
        except OSError:
            pass
        item["warnings"].append("image_not_ocr：图片只登记，不做 OCR")
        return item
    try:
        if suffix in TEXT_EXTS:
            got = _plain_text(path)
            if suffix in {".html", ".htm"}:
                got["text"] = _html_to_text(got["text"])
        elif suffix == ".docx":
            got = _docx_text(path)
        elif suffix == ".pptx":
            got = _pptx_text(path)
        elif suffix == ".xlsx":
            got = _xlsx_text(path)
        elif suffix == ".pdf":
            got = _pdf_text(path)
        elif suffix == ".eml":
            got = _eml_text(path)
        elif suffix in {".doc", ".wps", ".et", ".dps"}:
            got = {
                "text": "",
                "title": path.stem,
                "headings": [],
                "warnings": ["legacy_office_format：请另存为 .docx 或导出 PDF 后再给本工具"],
            }
        else:
            got = {
                "text": "",
                "title": path.stem,
                "headings": [],
                "warnings": [f"unsupported_ext：{suffix or '无扩展名'}"],
            }
    except Exception as exc:  # noqa: BLE001
        got = {"text": "", "title": path.stem, "headings": [], "warnings": [f"extract_error：{exc}"]}

    text = _clean(str(got.get("text") or ""))
    item["title"] = str(got.get("title") or path.stem)
    item["headings"] = list(got.get("headings") or [])
    item["images"] = list(got.get("images") or [])
    item["warnings"].extend(got.get("warnings") or [])
    for key in ("attachments", "messageId", "from", "to", "date"):
        if got.get(key):
            item[key] = got[key]
    item["chars"] = len(text)
    if max_chars > 0 and len(text) > max_chars:
        item["text"] = text[:max_chars] + f"\n\n…（已截断，原文 {len(text)} 字）"
        item["truncated"] = True
    else:
        item["text"] = text
    return item


def _iter_files(root: Path, max_files: int) -> tuple[list[Path], list[str]]:
    found: list[Path] = []
    warnings: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith("."))
        for name in filenames:
            if name.startswith("~$") or name.startswith("."):
                continue
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > 25 * 1024 * 1024:
                    warnings.append(f"skipped_large_file：{path}")
                    continue
            except OSError:
                continue
            found.append(path)
            if len(found) >= max_files * 4:
                break
        if len(found) >= max_files * 4:
            break
    def rank(p: Path) -> tuple[int, str]:
        return (PRIORITY.get(p.suffix.lower(), 20), str(p).lower())
    return sorted(found, key=rank)[:max_files], warnings


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Extract text from patent source materials")
    parser.add_argument("--input", default="-", help="JSON spec path or - for stdin")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8-sig")
        spec = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"bad_input：{exc}"}, ensure_ascii=False))
        return 1

    action = str(spec.get("action") or "file").lower()
    raw_path = spec.get("path")
    if not raw_path:
        print(json.dumps({"ok": False, "error": "missing_path"}, ensure_ascii=False))
        return 1
    path = Path(str(raw_path)).expanduser()
    if not path.exists():
        print(json.dumps({"ok": False, "error": f"path_not_found：{path}"}, ensure_ascii=False))
        return 1
    max_chars = int(spec.get("maxChars") or 20000)

    try:
        if action == "file" or path.is_file():
            item = extract_one(path, max_chars)
            print(json.dumps({"ok": True, "files": [item], "warnings": item.get("warnings", [])}, ensure_ascii=False))
            return 0
        max_files = max(1, min(int(spec.get("maxFiles") or 40), 120))
        files, warnings = _iter_files(path, max_files)
        if not files:
            print(json.dumps({"ok": True, "files": [], "warnings": warnings + ["目录内没有可读文件"]}, ensure_ascii=False))
            return 0
        items: list[dict[str, Any]] = []
        for file in files:
            item = extract_one(file, max_chars)
            item["path"] = str(item["path"])
            items.append(item)
        print(json.dumps({"ok": True, "files": items, "warnings": warnings}, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}：{exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
