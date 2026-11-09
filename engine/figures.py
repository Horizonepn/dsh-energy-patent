#!/usr/bin/env python
"""Render Chinese patent line-art figures (black on white) to PNG + SVG.

Spec (JSON on stdin or --input path):
{
  "dpi": 300,
  "figures": [
    {"id": "fig1", "kind": "flow",  "widthMm": 150, "spec": {...}},
    {"id": "fig2", "kind": "block", "widthMm": 150, "spec": {...}},
    {"id": "fig3", "kind": "chart", "widthMm": 150, "spec": {...}}
  ]
}

CLI:
  python figures.py --input spec.json [--out-dir DIR]
  python figures.py --input - [--out-dir DIR]

Prints one JSON object:
  {"ok": true, "files": [...], "warnings": []}
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

MM_PER_INCH = 25.4
DEFAULT_DPI = 300
BLACK = 0
WHITE = 255
STROKE = 0.35
LABEL_MM = 3.2
DETAIL_MM = 2.6
SMALL_MM = 2.4
TITLE_MM = 3.8
PAD_MM = 6.0
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]
FONT_PATH = next((p for p in FONT_CANDIDATES if Path(p).is_file()), None)
_FONT_CACHE: dict[int, Any] = {}


def utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def esc(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def font_for(size_mm: float, dpi: int) -> Any:
    px = max(6, int(round(size_mm * dpi / MM_PER_INCH)))
    if px in _FONT_CACHE:
        return _FONT_CACHE[px]
    if FONT_PATH is None:
        font = ImageFont.load_default()
    else:
        try:
            font = ImageFont.truetype(FONT_PATH, px)
        except Exception:
            font = ImageFont.load_default()
    _FONT_CACHE[px] = font
    return font


def text_width_mm(text: str, size_mm: float, dpi: int) -> float:
    font = font_for(size_mm, dpi)
    try:
        width_px = font.getlength(text)
    except Exception:
        width_px = len(text) * size_mm * dpi / MM_PER_INCH * 0.9
    return width_px * MM_PER_INCH / dpi


def wrap_text(text: str, max_width_mm: float, size_mm: float, dpi: int) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return [""]
    lines: list[str] = []
    for hard in raw.split("\n"):
        if not hard:
            lines.append("")
            continue
        current = ""
        for ch in hard:
            trial = current + ch
            if current and text_width_mm(trial, size_mm, dpi) > max_width_mm:
                # Prefer a break after Chinese punctuation or a space.
                cut = current
                for mark in ("，", "；", "、", "。", "：", " ", "）", ")"):
                    pos = current.rfind(mark)
                    if pos >= max(2, len(current) // 2):
                        cut = current[: pos + 1]
                        break
                lines.append(cut.strip())
                rest = current[len(cut):] if cut != current else ""
                current = (rest + ch).strip() if rest else ch
            else:
                current = trial
        if current:
            lines.append(current.strip())
    return lines or [""]


class Scene:
    """A display list in millimetres, shared by the PNG and SVG renderers."""

    def __init__(self, width_mm: float, dpi: int = DEFAULT_DPI) -> None:
        self.width_mm = float(width_mm)
        self.dpi = int(dpi)
        self.items: list[dict[str, Any]] = []
        self.x0 = math.inf
        self.y0 = math.inf
        self.x1 = -math.inf
        self.y1 = -math.inf
        self.warnings: list[str] = []

    # ---- bounds -------------------------------------------------------
    def _grow(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0 = min(self.x0, x0)
        self.y0 = min(self.y0, y0)
        self.x1 = max(self.x1, x1)
        self.y1 = max(self.y1, y1)

    def bounds(self) -> tuple[float, float, float, float]:
        if not self.items:
            return 0.0, 0.0, self.width_mm, 60.0
        return self.x0, self.y0, self.x1, self.y1

    # ---- primitives ---------------------------------------------------
    def rect(self, x: float, y: float, w: float, h: float, *, dashed: bool = False,
             width: float = STROKE, radius: float = 0.0, fill: bool = False) -> None:
        self.items.append({
            "kind": "rect", "x": x, "y": y, "w": w, "h": h, "dashed": dashed,
            "width": width, "radius": radius, "fill": fill,
        })
        self._grow(x - width, y - width, x + w + width, y + h + width)

    def line(self, points: list[tuple[float, float]], *, width: float = STROKE,
             dashed: bool = False, arrow: bool = False, arrow_start: bool = False) -> None:
        pts = [(float(a), float(b)) for a, b in points]
        if len(pts) < 2:
            return
        self.items.append({
            "kind": "line", "points": pts, "width": width, "dashed": dashed,
            "arrow": arrow, "arrow_start": arrow_start,
        })
        for x, y in pts:
            self._grow(x - width, y - width, x + width, y + width)

    def text(self, x: float, y: float, value: str, *, size: float = LABEL_MM,
             anchor: str = "middle", max_width: float | None = None) -> list[str]:
        lines = wrap_text(value, max_width, size, self.dpi) if max_width else str(value).split("\n")
        line_h = size * 1.32
        total_h = line_h * len(lines)
        top = y - total_h / 2 + line_h / 2
        for index, line in enumerate(lines):
            self.items.append({
                "kind": "text", "x": x, "y": top + index * line_h, "text": line,
                "size": size, "anchor": anchor,
            })
            width = text_width_mm(line, size, self.dpi)
            if anchor == "middle":
                left = x - width / 2
            elif anchor == "end":
                left = x - width
            else:
                left = x
            self._grow(left, top + index * line_h - size, left + width, top + index * line_h + size)
        return lines

    def polygon(self, points: list[tuple[float, float]], *, fill: bool = True) -> None:
        pts = [(float(a), float(b)) for a, b in points]
        self.items.append({"kind": "polygon", "points": pts, "fill": fill})
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self._grow(min(xs), min(ys), max(xs), max(ys))

    def circle(self, x: float, y: float, r: float, *, fill: bool = True) -> None:
        self.items.append({"kind": "circle", "x": x, "y": y, "r": r, "fill": fill})
        self._grow(x - r, y - r, x + r, y + r)

    # ---- arrow --------------------------------------------------------
    def arrow_head(self, start: tuple[float, float], end: tuple[float, float], length: float = 2.6) -> None:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        norm = math.hypot(dx, dy) or 1.0
        ux, uy = dx / norm, dy / norm
        px, py = -uy, ux
        base = (end[0] - ux * length, end[1] - uy * length)
        half = length * 0.42
        self.polygon([
            end,
            (base[0] + px * half, base[1] + py * half),
            (base[0] - px * half, base[1] - py * half),
        ], fill=True)

    # ---- rendering ----------------------------------------------------
    def _offset(self) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = self.bounds()
        pad = PAD_MM
        return x0 - pad, y0 - pad, (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad

    def render_png(self, path: Path) -> tuple[int, int]:
        ox, oy, width_mm, height_mm = self._offset()
        scale = self.dpi / MM_PER_INCH
        width_px = max(20, int(round(width_mm * scale)))
        height_px = max(20, int(round(height_mm * scale)))
        image = Image.new("L", (width_px, height_px), WHITE)
        draw = ImageDraw.Draw(image)

        def sx(x: float) -> float:
            return (x - ox) * scale

        def sy(y: float) -> float:
            return (y - oy) * scale

        for item in self.items:
            kind = item["kind"]
            if kind == "rect":
                x, y = sx(item["x"]), sy(item["y"])
                w, h = item["w"] * scale, item["h"] * scale
                lw = max(1, int(round(item["width"] * scale)))
                if item["radius"] > 0:
                    draw.rounded_rectangle([x, y, x + w, y + h], radius=item["radius"] * scale,
                                           outline=BLACK, width=lw, fill=WHITE if item["fill"] else None)
                elif item["dashed"]:
                    _dashed_rect(draw, x, y, w, h, lw)
                else:
                    draw.rectangle([x, y, x + w, y + h], outline=BLACK, width=lw,
                                   fill=WHITE if item["fill"] else None)
            elif kind == "line":
                pts = [(sx(x), sy(y)) for x, y in item["points"]]
                lw = max(1, int(round(item["width"] * scale)))
                if item["dashed"]:
                    for a, b in zip(pts, pts[1:]):
                        _dashed_segment(draw, a, b, lw)
                else:
                    draw.line(pts, fill=BLACK, width=lw, joint="curve")
                if item["arrow"]:
                    self._png_arrow(draw, pts[-2], pts[-1], scale)
                if item["arrow_start"]:
                    self._png_arrow(draw, pts[1], pts[0], scale)
            elif kind == "text":
                font = font_for(item["size"], self.dpi)
                draw.text((sx(item["x"]), sy(item["y"])), item["text"], font=font, fill=BLACK,
                          anchor={"middle": "mm", "start": "lm", "end": "rm"}[item["anchor"]])
            elif kind == "polygon":
                pts = [(sx(x), sy(y)) for x, y in item["points"]]
                draw.polygon(pts, fill=BLACK if item["fill"] else WHITE, outline=BLACK)
            elif kind == "circle":
                x, y, r = sx(item["x"]), sy(item["y"]), item["r"] * scale
                draw.ellipse([x - r, y - r, x + r, y + r], fill=BLACK if item["fill"] else WHITE,
                             outline=BLACK, width=max(1, int(round(STROKE * scale))))
        # Pure black and white: no grey anti-aliasing pixels.
        image = image.point(lambda p: 0 if p < 200 else 255).convert("1")
        image.save(path, "PNG", dpi=(self.dpi, self.dpi))
        return width_px, height_px

    def _png_arrow(self, draw: ImageDraw.ImageDraw, start: tuple[float, float],
                   end: tuple[float, float], scale: float) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        norm = math.hypot(dx, dy) or 1.0
        ux, uy = dx / norm, dy / norm
        px, py = -uy, ux
        length = 2.6 * scale
        half = length * 0.42
        base = (end[0] - ux * length, end[1] - uy * length)
        draw.polygon([
            end,
            (base[0] + px * half, base[1] + py * half),
            (base[0] - px * half, base[1] - py * half),
        ], fill=BLACK)

    def render_svg(self, path: Path) -> tuple[float, float]:
        ox, oy, width_mm, height_mm = self._offset()
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.2f}mm" '
            f'height="{height_mm:.2f}mm" viewBox="0 0 {width_mm:.2f} {height_mm:.2f}">',
            f'<rect x="0" y="0" width="{width_mm:.2f}" height="{height_mm:.2f}" fill="#fff"/>',
        ]

        def tx(x: float) -> float:
            return x - ox

        def ty(y: float) -> float:
            return y - oy

        for item in self.items:
            kind = item["kind"]
            if kind == "rect":
                extra = f' stroke-dasharray="2.2 1.4"' if item["dashed"] else ""
                radius = f' rx="{item["radius"]:.2f}"' if item["radius"] > 0 else ""
                fill = "#fff" if item["fill"] else "#fff"
                parts.append(
                    f'<rect x="{tx(item["x"]):.2f}" y="{ty(item["y"]):.2f}" '
                    f'width="{item["w"]:.2f}" height="{item["h"]:.2f}" fill="{fill}" '
                    f'stroke="#000" stroke-width="{item["width"]:.2f}"{extra}{radius}/>'
                )
            elif kind == "line":
                pts = " ".join(f'{tx(x):.2f},{ty(y):.2f}' for x, y in item["points"])
                extra = ' stroke-dasharray="2.2 1.4"' if item["dashed"] else ""
                parts.append(
                    f'<polyline points="{pts}" fill="none" stroke="#000" '
                    f'stroke-width="{item["width"]:.2f}" stroke-linejoin="round"{extra}/>'
                )
                if item["arrow"]:
                    self._svg_arrow(parts, item["points"][-2], item["points"][-1], tx, ty)
                if item["arrow_start"]:
                    self._svg_arrow(parts, item["points"][1], item["points"][0], tx, ty)
            elif kind == "text":
                anchor = {"middle": "middle", "start": "start", "end": "end"}[item["anchor"]]
                parts.append(
                    f'<text x="{tx(item["x"]):.2f}" y="{ty(item["y"]):.2f}" '
                    f'text-anchor="{anchor}" dominant-baseline="central" '
                    f'font-family="SimSun, Songti SC, serif" font-size="{item["size"]:.2f}" '
                    f'fill="#000">{esc(item["text"])}</text>'
                )
            elif kind == "polygon":
                pts = " ".join(f'{tx(x):.2f},{ty(y):.2f}' for x, y in item["points"])
                parts.append(f'<polygon points="{pts}" fill="#000" stroke="#000" stroke-width="0.2"/>')
            elif kind == "circle":
                parts.append(
                    f'<circle cx="{tx(item["x"]):.2f}" cy="{ty(item["y"]):.2f}" r="{item["r"]:.2f}" '
                    f'fill="#000" stroke="#000" stroke-width="0.2"/>'
                )
        parts.append("</svg>")
        path.write_text("\n".join(parts) + "\n", encoding="utf-8")
        return width_mm, height_mm

    @staticmethod
    def _svg_arrow(parts: list[str], start: tuple[float, float], end: tuple[float, float],
                   tx: Any, ty: Any, length: float = 2.6) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        norm = math.hypot(dx, dy) or 1.0
        ux, uy = dx / norm, dy / norm
        px, py = -uy, ux
        base = (end[0] - ux * length, end[1] - uy * length)
        half = length * 0.42
        pts = [
            (end[0], end[1]),
            (base[0] + px * half, base[1] + py * half),
            (base[0] - px * half, base[1] - py * half),
        ]
        text = " ".join(f"{tx(x):.2f},{ty(y):.2f}" for x, y in pts)
        parts.append(f'<polygon points="{text}" fill="#000" stroke="#000" stroke-width="0.2"/>')


def _dashed_segment(draw: ImageDraw.ImageDraw, a: tuple[float, float], b: tuple[float, float],
                    width: int, dash: float = 10.0, gap: float = 6.0) -> None:
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length < 0.1:
        return
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        draw.line([(a[0] + ux * pos, a[1] + uy * pos), (a[0] + ux * end, a[1] + uy * end)],
                  fill=BLACK, width=width)
        pos += dash + gap


def _dashed_rect(draw: ImageDraw.ImageDraw, x: float, y: float, w: float, h: float, width: int) -> None:
    _dashed_segment(draw, (x, y), (x + w, y), width)
    _dashed_segment(draw, (x + w, y), (x + w, y + h), width)
    _dashed_segment(draw, (x + w, y + h), (x, y + h), width)
    _dashed_segment(draw, (x, y + h), (x, y), width)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def node_box_height(node: dict[str, Any], width_mm: float, dpi: int) -> tuple[float, list[str], list[str]]:
    label = str(node.get("label") or node.get("id") or "")
    detail = str(node.get("detail") or "")
    label_lines = wrap_text(label, max(8.0, width_mm - 8.0), LABEL_MM, dpi)
    detail_lines = wrap_text(detail, max(8.0, width_mm - 8.0), DETAIL_MM, dpi) if detail else []
    height = 7.0 + len(label_lines) * LABEL_MM * 1.25 + len(detail_lines) * DETAIL_MM * 1.3
    return max(13.0, height), label_lines, detail_lines


def render_flow(spec: dict[str, Any], width_mm: float, dpi: int) -> Scene:
    steps = [s for s in spec.get("steps") or [] if isinstance(s, dict)]
    if len(steps) < 2:
        raise ValueError("flow.steps 至少需要 2 个步骤")
    scene = Scene(width_mm, dpi)
    inner_w = width_mm - 16.0
    # Box width follows the longest wrapped line.
    widest = 0.0
    for step in steps:
        widest = max(widest, text_width_mm(str(step.get("label") or ""), LABEL_MM, dpi))
    box_w = min(inner_w, max(52.0, widest + 14.0))
    x = (width_mm - box_w) / 2
    y = 2.0
    gap = 9.0

    def draw_box(label: str, detail: str, top: float, *, rounded: bool = False,
                 size: float = LABEL_MM) -> float:
        label_lines = wrap_text(label, box_w - 10.0, size, dpi)
        detail_lines = wrap_text(detail, box_w - 10.0, DETAIL_MM, dpi) if detail else []
        height = max(13.0, 6.0 + len(label_lines) * size * 1.3 + len(detail_lines) * DETAIL_MM * 1.35)
        scene.rect(x, top, box_w, height, radius=3.0 if rounded else 0.0, fill=False)
        text_y = top + height / 2 - (len(detail_lines) * DETAIL_MM * 1.35) / 2
        scene.text(x + box_w / 2, text_y, label, size=size, max_width=box_w - 10.0)
        if detail_lines:
            scene.text(x + box_w / 2, top + height - 3.2 - len(detail_lines) * DETAIL_MM * 0.65,
                       detail, size=DETAIL_MM, max_width=box_w - 10.0)
        return top + height

    if spec.get("start"):
        bottom = draw_box(str(spec["start"]), "", y, rounded=True, size=LABEL_MM)
        next_top = bottom + gap
        scene.line([(width_mm / 2, bottom), (width_mm / 2, next_top - 0.2)], arrow=True)
        y = next_top
    for index, step in enumerate(steps):
        bottom = draw_box(str(step.get("label") or ""), str(step.get("detail") or ""), y)
        if index < len(steps) - 1:
            next_top = bottom + gap
            scene.line([(width_mm / 2, bottom), (width_mm / 2, next_top - 0.2)], arrow=True)
            y = next_top
        else:
            y = bottom
    if spec.get("end"):
        next_top = y + gap
        scene.line([(width_mm / 2, y), (width_mm / 2, next_top - 0.2)], arrow=True)
        draw_box(str(spec["end"]), "", next_top, rounded=True)
    return scene


def render_block(spec: dict[str, Any], width_mm: float, dpi: int) -> Scene:
    columns = [c for c in spec.get("columns") or [] if isinstance(c, dict)]
    extras = [e for e in spec.get("extras") or [] if isinstance(e, dict)]
    if not columns and not extras:
        raise ValueError("block 需要 columns 或 extras")
    scene = Scene(width_mm, dpi)
    margin = 4.0
    gap = 10.0
    node_gap = 7.0
    group_pad = 4.0
    title_h = 7.5
    boxes: dict[str, tuple[float, float, float, float]] = {}

    def draw_group(group: dict[str, Any], gx: float, gy: float, gw: float,
                   nodes_horizontal: bool = False) -> float:
        title = str(group.get("title") or group.get("id") or "")
        nodes = [n for n in group.get("nodes") or [] if isinstance(n, dict)]
        if not nodes:
            return 0.0
        widths: list[float] = []
        if nodes_horizontal:
            raw = (gw - 2 * group_pad - node_gap * (len(nodes) - 1)) / len(nodes)
            node_w = min(raw, 58.0)
            widths = [node_w] * len(nodes)
        else:
            node_w = gw - 2 * group_pad
            widths = [node_w] * len(nodes)
        heights = [node_box_height(node, width, dpi)[0] for node, width in zip(nodes, widths)]
        content_h = max(heights) if nodes_horizontal else sum(heights) + node_gap * (len(nodes) - 1)
        frame_h = title_h + group_pad + content_h + group_pad
        scene.rect(gx, gy, gw, frame_h, dashed=True, width=0.3)
        if title:
            scene.text(gx + 3.0, gy + title_h / 2, title, size=DETAIL_MM, anchor="start")
        cy = gy + title_h + group_pad
        if nodes_horizontal:
            total_w = sum(widths) + node_gap * (len(nodes) - 1)
            nx = gx + (gw - total_w) / 2
            for node, node_w, h in zip(nodes, widths, heights):
                nid = str(node.get("id") or node.get("label") or "")
                boxes[nid] = (nx, cy, node_w, h)
                _draw_node(scene, node, nx, cy, node_w, h, dpi)
                nx += node_w + node_gap
        else:
            for node, node_w, h in zip(nodes, widths, heights):
                nid = str(node.get("id") or node.get("label") or "")
                boxes[nid] = (gx + group_pad, cy, node_w, h)
                _draw_node(scene, node, gx + group_pad, cy, node_w, h, dpi)
                cy += h + node_gap
        return frame_h

    y = 2.0
    if extras:
        ex = margin
        ew = width_mm - 2 * margin
        for extra in extras:
            h = draw_group(extra, ex, y, ew, nodes_horizontal=True)
            y += h + 10.0
    n = max(1, len(columns))
    col_w = (width_mm - 2 * margin - gap * (n - 1)) / n
    for index, column in enumerate(columns):
        gx = margin + index * (col_w + gap)
        draw_group(column, gx, y, col_w)
    # Links after boxes so arrows sit on top.
    for link in spec.get("links") or []:
        a = str(link.get("from") or "").strip()
        b = str(link.get("to") or "").strip()
        if a not in boxes or b not in boxes:
            raise ValueError(f"links 指向不存在的节点：{a} -> {b}")
        points = _route(boxes[a], boxes[b])
        scene.line(points, dashed=str(link.get("style") or "") == "dashed", arrow=True)
        label = str(link.get("label") or "")
        if label:
            _draw_link_label(scene, points, label, dpi)
    return scene


def _draw_link_label(scene: Scene, points: list[tuple[float, float]], label: str, dpi: int) -> None:
    if len(points) < 2:
        return
    # Choose the longest segment as the label anchor.
    best = max(zip(points, points[1:]), key=lambda pair: abs(pair[0][0] - pair[1][0]) + abs(pair[0][1] - pair[1][1]))
    a, b = best
    mid_x = (a[0] + b[0]) / 2
    mid_y = (a[1] + b[1]) / 2
    horizontal = abs(b[0] - a[0]) >= abs(b[1] - a[1])
    if horizontal:
        anchor_x, anchor_y, align = mid_x, mid_y - 3.2, "middle"
        max_w = 10.0
    else:
        anchor_x, anchor_y, align = mid_x + 1.4, mid_y, "start"
        max_w = 11.0
    lines = wrap_text(label, max_w, SMALL_MM, dpi)
    width = max(text_width_mm(line, SMALL_MM, dpi) for line in lines) + 1.6
    height = 2.0 + len(lines) * SMALL_MM * 1.3
    if align == "middle":
        rect_x = anchor_x - width / 2
    else:
        rect_x = anchor_x - 0.8
    scene.rect(rect_x, anchor_y - height / 2, width, height, fill=True, width=0.0)
    scene.text(anchor_x, anchor_y, label, size=SMALL_MM, anchor=align, max_width=max_w)


def _draw_node(scene: Scene, node: dict[str, Any], x: float, y: float, w: float, h: float, dpi: int) -> None:
    label = str(node.get("label") or node.get("id") or "")
    detail = str(node.get("detail") or "")
    scene.rect(x, y, w, h, fill=False)
    label_lines = wrap_text(label, w - 6.0, LABEL_MM, dpi)
    detail_lines = wrap_text(detail, w - 6.0, DETAIL_MM, dpi) if detail else []
    total = len(label_lines) * LABEL_MM * 1.3 + len(detail_lines) * DETAIL_MM * 1.35
    cy = y + h / 2 - total / 2 + LABEL_MM * 0.65
    scene.text(x + w / 2, cy, label, size=LABEL_MM, max_width=w - 6.0)
    if detail:
        scene.text(x + w / 2, y + h - 3.0 - len(detail_lines) * DETAIL_MM * 0.65, detail,
                   size=DETAIL_MM, max_width=w - 6.0)


def _route(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    acx, acy = ax + aw / 2, ay + ah / 2
    bcx, bcy = bx + bw / 2, by + bh / 2
    if abs(acx - bcx) < max(aw, bw) * 0.5:
        if acy < bcy:
            return [(acx, ay + ah), (bcx, by)]
        return [(acx, ay), (bcx, by + bh)]
    if acx < bcx:
        start = (ax + aw, acy)
        end = (bx, bcy)
    else:
        start = (ax, acy)
        end = (bx + bw, bcy)
    if abs(start[1] - end[1]) < 0.2:
        return [start, end]
    mid_x = (start[0] + end[0]) / 2
    return [start, (mid_x, start[1]), (mid_x, end[1]), end]


def render_chart(spec: dict[str, Any], width_mm: float, dpi: int) -> Scene:
    categories = [str(c) for c in spec.get("categories") or []]
    series = [s for s in spec.get("series") or [] if isinstance(s, dict)]
    if not categories or not series:
        raise ValueError("chart 需要 categories 与 series")
    chart_type = str(spec.get("chartType") or "bar").lower()
    values = [float(v) for s in series for v in (s.get("values") or [])]
    y_min = spec.get("yMin")
    y_max = spec.get("yMax")
    data_min = min(values) if values else 0.0
    data_max = max(values) if values else 1.0
    y_min = float(y_min) if y_min is not None else min(0.0, data_min)
    y_max = float(y_max) if y_max is not None else max(0.0, data_max * 1.12 if data_max else 1.0)
    if y_max <= y_min:
        y_max = y_min + 1.0
    scene = Scene(width_mm, dpi)
    left, right, top, bottom = 24.0, 8.0, 12.0, 20.0
    plot_w = width_mm - left - right
    plot_h = max(55.0, width_mm * 0.52)
    x0, y0 = left, top
    x1, y1 = left + plot_w, top + plot_h
    scene.line([(x0, y1), (x1 + 2.0, y1)], arrow=True)
    scene.line([(x0, y1), (x0, y0 - 2.0)], arrow=True)
    ticks = 5
    for i in range(ticks + 1):
        value = y_min + (y_max - y_min) * i / ticks
        y = y1 - (value - y_min) / (y_max - y_min) * plot_h
        label = _fmt(value)
        scene.text(x0 - 2.0, y, label, size=SMALL_MM, anchor="end")
        if 0 < i < ticks:
            scene.line([(x0, y), (x1, y)], width=0.18, dashed=True)
    slot_w = plot_w / len(categories)
    for index, category in enumerate(categories):
        cx = x0 + slot_w * (index + 0.5)
        scene.text(cx, y1 + 5.5, category, size=DETAIL_MM, max_width=slot_w - 2.0)
    if chart_type == "bar":
        group_w = slot_w * 0.68
        bar_w = group_w / max(1, len(series))
        for s_index, s in enumerate(series):
            for c_index, value in enumerate(s.get("values") or []):
                value = float(value)
                cx = x0 + slot_w * (c_index + 0.5) - group_w / 2 + bar_w * s_index
                y = y1 - (value - y_min) / (y_max - y_min) * plot_h
                scene.rect(cx, min(y, y1), bar_w, abs(y1 - y), fill=False)
                _hatch(scene, cx, min(y, y1), bar_w, abs(y1 - y), s_index)
    else:
        for s_index, s in enumerate(series):
            pts = []
            for c_index, value in enumerate(s.get("values") or []):
                cx = x0 + slot_w * (c_index + 0.5)
                cy = y1 - (float(value) - y_min) / (y_max - y_min) * plot_h
                pts.append((cx, cy))
            scene.line(pts, width=0.45)
            for px, py in pts:
                _marker(scene, px, py, s_index)
    # Legend
    legend_w = max(text_width_mm(str(s.get("name") or ""), SMALL_MM, dpi) for s in series) + 16.0
    legend_h = 5.0 * len(series) + 3.0
    lx = x1 - legend_w - 1.0
    ly = y0 + 1.0
    scene.rect(lx, ly, legend_w, legend_h, width=0.25, fill=True)
    for index, s in enumerate(series):
        sy = ly + 3.5 + index * 5.0
        if chart_type == "bar":
            scene.rect(lx + 3.0, sy - 1.8, 5.0, 3.6, fill=False)
            _hatch(scene, lx + 3.0, sy - 1.8, 5.0, 3.6, index)
        else:
            scene.line([(lx + 2.0, sy), (lx + 8.0, sy)], width=0.4)
            _marker(scene, lx + 5.0, sy, index)
        scene.text(lx + 11.0, sy, str(s.get("name") or ""), size=SMALL_MM, anchor="start")
    if spec.get("xLabel"):
        scene.text((x0 + x1) / 2, y1 + 13.0, str(spec["xLabel"]), size=DETAIL_MM)
    if spec.get("yLabel"):
        scene.text(x0, y0 - 5.0, str(spec["yLabel"]), size=DETAIL_MM, anchor="start")
    return scene


def _fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def _marker(scene: Scene, x: float, y: float, index: int) -> None:
    shape = index % 3
    if shape == 0:
        scene.circle(x, y, 1.1, fill=True)
    elif shape == 1:
        scene.rect(x - 1.0, y - 1.0, 2.0, 2.0, fill=True)
    else:
        scene.polygon([(x, y - 1.2), (x + 1.2, y + 1.0), (x - 1.2, y + 1.0)], fill=True)


def _hatch(scene: Scene, x: float, y: float, w: float, h: float, index: int) -> None:
    pattern = index % 4
    if pattern == 0:
        return
    step = 2.0
    if pattern == 1:
        start = x - h
        while start < x + w:
            _clip_add(scene, start, y + h, start + h, y, x, y, w, h)
            start += step
    elif pattern == 2:
        start = x - h
        while start < x + w:
            _clip_add(scene, start, y, start + h, y + h, x, y, w, h)
            start += step
    else:
        yy = y + step / 2
        while yy < y + h:
            scene.line([(x + 0.6, yy), (x + w - 0.6, yy)], width=0.16)
            yy += step


def _clip_add(scene: Scene, x1: float, y1: float, x2: float, y2: float,
              rx: float, ry: float, rw: float, rh: float) -> None:
    seg = _clip(x1, y1, x2, y2, rx, ry, rx + rw, ry + rh)
    if seg:
        scene.line([seg[0], seg[1]], width=0.16)


def _clip(x1: float, y1: float, x2: float, y2: float,
          rx1: float, ry1: float, rx2: float, ry2: float):
    """Liang-Barsky clip of a segment against a rectangle."""
    dx, dy = x2 - x1, y2 - y1
    p = [-dx, dx, -dy, dy]
    q = [x1 - rx1, rx2 - x1, y1 - ry1, ry2 - y1]
    u1, u2 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            if qi < 0:
                return None
            continue
        t = qi / pi
        if pi < 0:
            u1 = max(u1, t)
        else:
            u2 = min(u2, t)
        if u1 > u2:
            return None
    return ((x1 + u1 * dx, y1 + u1 * dy), (x1 + u2 * dx, y1 + u2 * dy))


def render_one(fig: dict[str, Any], out_dir: Path, default_dpi: int, default_width: float) -> dict[str, Any]:
    fid = str(fig.get("id") or "").strip()
    if not fid or not all(c.isalnum() or c in "-_" for c in fid):
        raise ValueError(f"非法图号：{fid!r}（只用字母、数字、-、_）")
    kind = str(fig.get("kind") or "").lower()
    width_mm = float(fig.get("widthMm") or default_width)
    if width_mm < 60 or width_mm > 180:
        raise ValueError("widthMm 须在 60–180 之间")
    spec = fig.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"{fid}: spec 必须是对象")
    dpi = int(fig.get("dpi") or default_dpi)
    if kind == "flow":
        scene = render_flow(spec, width_mm, dpi)
    elif kind == "block":
        scene = render_block(spec, width_mm, dpi)
    elif kind == "chart":
        scene = render_chart(spec, width_mm, dpi)
    else:
        raise ValueError(f"{fid}: 不支持的图型 {kind}（flow/block/chart）")
    png = out_dir / f"{fid}.png"
    svg = out_dir / f"{fid}.svg"
    width_px, height_px = scene.render_png(png)
    width_svg, height_svg = scene.render_svg(svg)
    return {
        "id": fid,
        "kind": kind,
        "png": str(png),
        "svg": str(svg),
        "widthMm": round(width_svg, 2),
        "heightMm": round(height_svg, 2),
        "pixels": [width_px, height_px],
        "dpi": dpi,
        "warnings": scene.warnings,
    }


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    parser = argparse.ArgumentParser(description="Render CNIPA-style patent figures")
    parser.add_argument("--input", default="-", help="JSON spec path, or - for stdin")
    parser.add_argument("--out-dir", default="", help="output directory")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8-sig")
        spec = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"bad_input: {exc}"}, ensure_ascii=False))
        return 1
    figures = spec.get("figures")
    if not isinstance(figures, list) or not figures:
        print(json.dumps({"ok": False, "error": "figures 必须是非空数组"}, ensure_ascii=False))
        return 1
    out_dir = Path(args.out_dir or spec.get("outDir") or ".").expanduser().resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"cannot create outDir: {exc}"}, ensure_ascii=False))
        return 1
    dpi = int(spec.get("dpi") or DEFAULT_DPI)
    default_width = float(spec.get("widthMm") or 150.0)
    files = []
    warnings = []
    try:
        for figure in figures:
            if not isinstance(figure, dict):
                raise ValueError("figures 内每个元素必须是对象")
            info = render_one(figure, out_dir, dpi, default_width)
            files.append(info)
            warnings.extend(info.get("warnings") or [])
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "files": files, "warnings": warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
