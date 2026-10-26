"""Bounded, read-only snapshotting of ASCII DXF records.

This intentionally records geometry and text only; it makes no device or
connectivity claims.  DXF files are consumed as group-code/value pairs so a
large drawing is never loaded through ezdxf's whole-file reader.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .cad_geometry import GeometryBudgetError, transform_curve

DEFAULT_LIMITS = {
    "max_seconds": 120.0,
    "max_records_per_scope": 2_000_000,
    "max_supported_records": 100_000,
    "max_retained_records": 100_000,
    "max_block_count": 20_000,
    "max_insert_depth": 32,
    "max_record_pairs": 2_000,
    "max_record_chars": 2_000_000,
    "max_line_chars": 1_000_000,
    "max_output_bytes": 32_000_000,
    "max_geometry_points": 200_000,
    "max_path_points": 4096,
}
SUPPORTED = {"TEXT", "MTEXT", "ATTRIB", "INSERT", "LINE", "ARC", "CIRCLE", "LWPOLYLINE"}
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
UNICODE_ESCAPE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


class SnapshotError(ValueError):
    pass


def _decode(value: str) -> str:
    def convert(match: re.Match[str]) -> str:
        codepoint = int(match.group(1), 16)
        if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
            return match.group(0)
        return chr(codepoint)

    return UNICODE_ESCAPE.sub(convert, value)


def _pairs(path: Path, max_line_chars: int) -> Iterator[tuple[str, str]]:
    # Modern ASCII DXF stores strings as UTF-8. Older drawings use the declared
    # code page. Refuse an unsupported encoding instead of corrupting labels.
    with path.open("rb") as stream:
        prefix = stream.read(65536)
    if prefix.startswith(b"AutoCAD Binary DXF"):
        raise SnapshotError("Binary DXF is unsupported; export an ASCII DXF")
    version = re.search(rb"\$ACADVER\s*\r?\n\s*1\s*\r?\n(AC\d+)", prefix)
    codepage = re.search(rb"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]+)", prefix)
    modern = bool(version and int(version[1][2:]) >= 1021)
    encoding = "utf-8-sig" if modern else "cp1252"
    if (
        not modern
        and codepage
        and codepage[1].strip() not in {b"ANSI_1252", b"UTF-8", b"UTF8"}
    ):
        raise SnapshotError("Unsupported legacy DXF code page; export UTF-8 DXF")
    if codepage and codepage[1].strip() in {b"UTF-8", b"UTF8"}:
        encoding = "utf-8-sig"
    with path.open("r", encoding=encoding, errors="strict", newline="") as f:
        while True:
            code = f.readline(max_line_chars + 2)
            if not code:
                return
            if len(code.rstrip("\r\n")) > max_line_chars:
                raise SnapshotError("DXF group-code line exceeds max_line_chars")
            value = f.readline(max_line_chars + 2)
            if not value:
                raise SnapshotError("unterminated DXF group-code/value pair")
            if len(value.rstrip("\r\n")) > max_line_chars:
                raise SnapshotError("DXF value line exceeds max_line_chars")
            try:
                number = str(int(code.strip()))
            except ValueError as exc:
                raise SnapshotError(f"invalid DXF group code: {code[:40]!r}") from exc
            yield number, value.rstrip("\r\n")


def _number(record: dict[str, list[str]], code: str, default: float = 0.0) -> float:
    if code not in record:
        return default
    try:
        value = float(record[code][0].strip())
    except (ValueError, TypeError) as exc:
        raise SnapshotError(
            f"invalid numeric DXF group {code}: {record[code][0]!r}"
        ) from exc
    if not math.isfinite(value):
        raise SnapshotError(f"non-finite numeric DXF group {code}: {record[code][0]!r}")
    return value


def _integer(record: dict[str, list[str]], code: str, default: int = 0) -> int:
    value = _number(record, code, float(default))
    if not value.is_integer():
        raise SnapshotError(f"non-integer DXF group {code}: {value!r}")
    return int(value)


def _checked_limits(limits: dict[str, float | int] | None) -> dict[str, float | int]:
    cfg = DEFAULT_LIMITS | (limits or {})
    if set(cfg) != set(DEFAULT_LIMITS):
        unknown = sorted(set(cfg) - set(DEFAULT_LIMITS))
        raise SnapshotError(f"unknown snapshot limit(s): {unknown}")
    for key, value in cfg.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SnapshotError(f"limit {key} must be a positive number")
        try:
            valid = math.isfinite(float(value)) and value > 0
        except (OverflowError, TypeError, ValueError):
            valid = False
        if not valid:
            raise SnapshotError(f"limit {key} must be positive and finite")
        if key != "max_seconds" and (not isinstance(value, int) or value <= 0):
            raise SnapshotError(f"limit {key} must be a positive integer")
    return cfg


def _stat_signature(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _paper_space(record: dict[str, list[str]]) -> bool:
    layout = _decode(_first(record, "410")).strip()
    return _first(record, "67").strip() == "1" or bool(
        layout and layout.casefold() != "model"
    )


def _record_omission(
    record_type: str,
    scope: str,
    record: dict[str, list[str]],
    block: dict[str, Any] | None,
    omissions: Counter[str],
    unsupported_by_scope: Counter[str],
    non_model_counts: Counter[str],
    reason: str = "unsupported",
) -> None:
    if scope == "BLOCKS" and block is not None:
        block["unsupported_counts"][f"{reason}:{record_type}"] += 1
    elif scope == "ENTITIES":
        if _paper_space(record):
            non_model_counts[record_type] += 1
        else:
            omissions[f"{reason}:ENTITIES:{record_type}"] += 1
    else:
        unsupported_by_scope[f"{scope}:{record_type}"] += 1


def _first(record: dict[str, list[str]], code: str, default: str = "") -> str:
    return record.get(code, [default])[0]


def _compose(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    """Return a after b, where each tuple maps (x,y) to (x',y')."""
    a11, a12, a21, a22, atx, aty = a
    b11, b12, b21, b22, btx, bty = b
    result = (
        a11 * b11 + a12 * b21,
        a11 * b12 + a12 * b22,
        a21 * b11 + a22 * b21,
        a21 * b12 + a22 * b22,
        a11 * btx + a12 * bty + atx,
        a21 * btx + a22 * bty + aty,
    )
    if not all(math.isfinite(value) for value in result):
        raise SnapshotError("non-finite coordinate produced by INSERT transform")
    return result


def _point(m: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    point = (m[0] * x + m[1] * y + m[4], m[2] * x + m[3] * y + m[5])
    if not all(math.isfinite(value) for value in point):
        raise SnapshotError("non-finite coordinate produced by INSERT transform")
    return point


def _insert_matrix(
    record: dict[str, list[str]], base: tuple[float, float]
) -> tuple[float, ...]:
    # The stream parser normalizes INSERT fields before transform expansion.
    x = float(record.get("x", _number(record, "10")))
    y = float(record.get("y", _number(record, "20")))
    sx = float(record.get("sx", _number(record, "41", 1)))
    sy = float(record.get("sy", _number(record, "42", 1)))
    theta = math.radians(float(record.get("rotation", _number(record, "50"))))
    c, s = math.cos(theta), math.sin(theta)
    return (
        c * sx,
        -s * sy,
        s * sx,
        c * sy,
        x - (c * sx * base[0] - s * sy * base[1]),
        y - (s * sx * base[0] + c * sy * base[1]),
    )


def _unsupported_extrusion(record: dict[str, list[str]]) -> bool:
    x, y, z = (_number(record, k, d) for k, d in (("210", 0), ("220", 0), ("230", 1)))
    return abs(x) > 1e-10 or abs(y) > 1e-10 or abs(z - 1) > 1e-10


def snapshot_dxf(
    path: str | Path, limits: dict[str, float | int] | None = None
) -> dict[str, Any]:
    """Read one ASCII DXF into a capped JSON-serializable locator snapshot."""
    source = Path(path)
    if not source.is_file():
        raise SnapshotError(f"not a regular input file: {source}")
    cfg = _checked_limits(limits)
    started = time.monotonic()
    initial_stat = source.stat()
    size = initial_stat.st_size
    initial_signature = _stat_signature(initial_stat)
    digest = hashlib.sha256()
    with source.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if time.monotonic() - started > float(cfg["max_seconds"]):
                raise SnapshotError("time budget exceeded while hashing source")
            digest.update(chunk)
    if _stat_signature(source.stat()) != initial_signature:
        raise SnapshotError("source changed while its hash was being computed")
    types: Counter[str] = Counter()
    scopes: Counter[str] = Counter()
    blocks: dict[str, dict[str, Any]] = {}
    entities: list[dict[str, Any]] = []
    warnings: list[str] = []
    omissions: Counter[str] = Counter()
    unsupported_by_scope: Counter[str] = Counter()
    non_model_counts: Counter[str] = Counter()
    header: dict[str, str] = {}
    section = ""
    expect_section_name = False
    current_var = ""
    scope = ""
    block: dict[str, Any] | None = None
    record_type: str | None = None
    record: dict[str, list[str]] = {}
    vertex_pairs: list[tuple[str, str]] = []
    record_pairs = 0
    record_chars = 0
    record_truncated = False
    eof_seen = False
    limits_hit = False
    retained_records = 0

    def save_record() -> None:
        nonlocal block, retained_records
        if not record_type:
            return
        if record_truncated:
            _record_omission(
                record_type,
                scope,
                record,
                block,
                omissions,
                unsupported_by_scope,
                non_model_counts,
                "oversized_record",
            )
            return
        if scope == "BLOCKS" and record_type == "BLOCK":
            name = _first(record, "2") or _first(record, "3")
            block = {
                "name": name,
                "handle": _first(record, "5"),
                "base": [_number(record, "10"), _number(record, "20")],
                "base_z": _number(record, "30"),
                "flags": _integer(record, "70"),
                "unsupported_counts": Counter(),
                "index_incomplete": False,
            }
            if len(blocks) >= int(cfg["max_block_count"]):
                unsupported_by_scope["block_definition_cap"] += 1
            else:
                blocks[name] = {**block, "items": []}
                block = blocks[name]
            return
        if record_type == "ENDBLK":
            block = None
            return
        if record_type not in SUPPORTED:
            if record_type not in {"ENDSEC", "ENDTAB", "SECTION", "EOF"}:
                _record_omission(
                    record_type,
                    scope,
                    record,
                    block,
                    omissions,
                    unsupported_by_scope,
                    non_model_counts,
                )
            return
        if scope == "ENTITIES" and _paper_space(record):
            non_model_counts[record_type] += 1
            return
        if scopes[scope] >= int(cfg["max_records_per_scope"]):
            if scope == "BLOCKS" and block is not None:
                block["index_incomplete"] = True
                unsupported_by_scope["scope_record_cap:BLOCKS"] += 1
            else:
                omissions[f"scope_record_cap:{scope}"] += 1
            return
        scopes[scope] += 1
        if retained_records >= int(cfg["max_retained_records"]):
            if scope == "BLOCKS" and block is not None:
                block["index_incomplete"] = True
                unsupported_by_scope["retained_record_cap:BLOCKS"] += 1
            else:
                omissions[f"retained_record_cap:{scope}"] += 1
            return
        retained_records += 1
        layout = _decode(_first(record, "410")).strip()
        item = {
            "kind": record_type,
            "handle": _first(record, "5"),
            "layer": _decode(_first(record, "8", "0")),
            "layout": layout,
            "paper_space": _paper_space(record),
            "invisible": bool(_integer(record, "60", 0)),
        }
        if record_type in {"TEXT", "MTEXT", "ATTRIB"}:
            text = "".join(
                value for code in ("3", "1") for value in record.get(code, [])
            )
            item.update(
                text=_decode(text),
                x=_number(record, "10"),
                y=_number(record, "20"),
                z=_number(record, "30"),
                text_height=_number(record, "40"),
                text_rotation=_number(record, "50"),
                text_attachment=_integer(record, "71", 1)
                if record_type == "MTEXT"
                else 7,
            )
        elif record_type == "LINE":
            item.update(
                x1=_number(record, "10"),
                y1=_number(record, "20"),
                z1=_number(record, "30"),
                x2=_number(record, "11"),
                y2=_number(record, "21"),
                z2=_number(record, "31"),
            )
        elif record_type in {"ARC", "CIRCLE"}:
            item.update(
                x=_number(record, "10"),
                y=_number(record, "20"),
                z=_number(record, "30"),
                radius=_number(record, "40"),
                start_angle=_number(record, "50"),
                end_angle=_number(record, "51"),
                unsupported_extrusion=_unsupported_extrusion(record),
            )
            if item["radius"] <= 0:
                raise SnapshotError("A native curve has a nonpositive radius")
        elif record_type == "LWPOLYLINE":
            vertices = []
            for code, value in vertex_pairs:
                if code == "10":
                    vertices.append({"x": _number({code: [value]}, code), "bulge": 0.0})
                elif code in {"20", "42", "40", "41"}:
                    if not vertices:
                        raise SnapshotError(
                            "Polyline vertex values precede its X coordinate"
                        )
                    key = {
                        "20": "y",
                        "42": "bulge",
                        "40": "start_width",
                        "41": "end_width",
                    }[code]
                    vertices[-1][key] = _number({code: [value]}, code)
            if any("y" not in v for v in vertices) or len(vertices) < 2:
                raise SnapshotError("A native polyline has incomplete vertices")
            if _integer(record, "90", len(vertices)) != len(vertices):
                raise SnapshotError(
                    "Polyline vertex count does not match its native data"
                )
            item.update(
                vertices=vertices,
                closed=bool(_integer(record, "70") & 1),
                z=_number(record, "38"),
                constant_width=_number(record, "43"),
                unsupported_extrusion=_unsupported_extrusion(record),
            )
        elif record_type == "INSERT":
            item.update(
                block=_first(record, "2"),
                x=_number(record, "10"),
                y=_number(record, "20"),
                z=_number(record, "30"),
                sx=_number(record, "41", 1),
                sy=_number(record, "42", 1),
                sz=_number(record, "43", 1),
                rotation=_number(record, "50"),
                columns=_integer(record, "70", 1),
                rows=_integer(record, "71", 1),
                unsupported_extrusion=_unsupported_extrusion(record),
                # Scaling Z cannot move planar child geometry at Z=0. Nonzero
                # insertion/base/child elevations are still explicitly omitted.
                unsupported_z_transform=abs(_number(record, "30")) > 1e-10,
            )
        if record_type in {"TEXT", "MTEXT", "ATTRIB"}:
            item["unsupported_extrusion"] = _unsupported_extrusion(record)
        if scope == "BLOCKS" and block is not None:
            if block["name"] in blocks:
                blocks[block["name"]]["items"].append(item)
            return
        if scope == "ENTITIES":
            entities.append(item)

    try:
        iterator = _pairs(source, int(cfg["max_line_chars"]))
        for code, value in iterator:
            if time.monotonic() - started > float(cfg["max_seconds"]):
                omissions["time_budget"] += 1
                limits_hit = True
                break
            if code == "0":
                save_record()
                record_type, record, record_pairs, record_chars, record_truncated = (
                    value,
                    {},
                    0,
                    0,
                    False,
                )
                vertex_pairs = []
                types[f"{scope}:{value}"] += 1
                if value == "SECTION":
                    section = ""
                    expect_section_name = True
                elif value == "ENDSEC":
                    section = scope = ""
                elif value == "EOF":
                    eof_seen = True
                    break
                elif value == "ENDTAB":
                    pass
                continue
            record_pairs += 1
            record_chars += len(code) + len(value)
            if record_pairs > int(cfg["max_record_pairs"]) or record_chars > int(
                cfg["max_record_chars"]
            ):
                record_truncated = True
                continue
            if expect_section_name:
                expect_section_name = False
            if record_type == "SECTION" and code == "2" and section == "":
                section = scope = value
                continue
            if section == "HEADER":
                if code == "9":
                    current_var = value
                elif current_var:
                    header[current_var] = value
            record.setdefault(code, []).append(value)
            if record_type == "LWPOLYLINE" and code in {"10", "20", "42", "40", "41"}:
                vertex_pairs.append((code, value))
        save_record()
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError(f"failed while streaming DXF: {exc}") from exc

    if not eof_seen:
        warnings.append("DXF ended without a valid 0/EOF record")
    if not blocks:
        warnings.append("no BLOCK definitions indexed")
    if (header.get("$INSUNITS") or "").strip() == "1":
        units = "inches"
    elif (header.get("$INSUNITS") or "").strip() == "0":
        units = "unitless"
    else:
        units = f"INSUNITS:{(header.get('$INSUNITS') or 'unknown').strip()}"

    placed_text: list[dict[str, Any]] = []
    placed_lines: list[dict[str, Any]] = []
    placed_inserts: list[dict[str, Any]] = []
    placed_paths: list[dict[str, Any]] = []
    geometry_points = 0
    reachable_blocks: set[str] = set()
    reachable_block_unsupported: Counter[str] = Counter()
    emitted = 0
    supported_seen = sum(scopes.values())

    def put(target: list[dict[str, Any]], value: dict[str, Any]) -> bool:
        nonlocal emitted
        if emitted >= int(cfg["max_supported_records"]):
            omissions["located_output_cap"] += 1
            return False
        target.append(value)
        emitted += 1
        return True

    def walk(
        item: dict[str, Any],
        transform: tuple[float, ...],
        path_names: tuple[str, ...],
        source_handle: str,
        loc: str,
        inherited_layer: str,
        root_context: dict[str, Any],
        inherited_invisible: bool = False,
    ) -> None:
        nonlocal limits_hit, geometry_points
        if limits_hit:
            return
        if time.monotonic() - started > float(cfg["max_seconds"]):
            omissions["time_budget"] += 1
            limits_hit = True
            return
        if emitted >= int(cfg["max_supported_records"]):
            omissions["located_output_cap"] += 1
            return
        kind = item["kind"]
        effective_layer = inherited_layer if item["layer"] == "0" else item["layer"]
        common = {
            "layer": effective_layer,
            "layout": root_context["layout"],
            "root_handle": root_context["handle"],
            "root_location": root_context["location"],
            "invisible": inherited_invisible or item["invisible"],
        }
        if kind in {"TEXT", "MTEXT", "ATTRIB"}:
            if item["unsupported_extrusion"]:
                omissions["unsupported_text_extrusion"] += 1
                return
            if abs(item["z"]) > 1e-10:
                omissions["nonplanar_text"] += 1
                return
            x, y = _point(transform, item["x"], item["y"])
            angle = math.radians(item["text_rotation"])
            cosine, sine = math.cos(angle), math.sin(angle)
            put(
                placed_text,
                {
                    "kind": kind,
                    "text": item["text"],
                    "x": x,
                    "y": y,
                    "z": item["z"],
                    "text_height": abs(item["text_height"])
                    * math.hypot(
                        -transform[0] * sine + transform[1] * cosine,
                        -transform[2] * sine + transform[3] * cosine,
                    ),
                    "text_rotation": math.degrees(
                        math.atan2(
                            transform[2] * cosine + transform[3] * sine,
                            transform[0] * cosine + transform[1] * sine,
                        )
                    ),
                    "text_attachment": item["text_attachment"],
                    "handle": item["handle"],
                    "source_handle": source_handle,
                    **common,
                    "block_path": list(path_names),
                    "locator": loc,
                },
            )
        elif kind == "LINE":
            if abs(item["z1"]) > 1e-10 or abs(item["z2"]) > 1e-10:
                omissions["nonplanar_line"] += 1
                return
            p1 = _point(transform, item["x1"], item["y1"])
            p2 = _point(transform, item["x2"], item["y2"])
            put(
                placed_lines,
                {
                    "kind": "LINE",
                    "start": [*p1, item["z1"]],
                    "end": [*p2, item["z2"]],
                    "handle": item["handle"],
                    "source_handle": source_handle,
                    **common,
                    "block_path": list(path_names),
                    "locator": loc,
                },
            )
        elif kind in {"ARC", "CIRCLE", "LWPOLYLINE"}:
            if item["unsupported_extrusion"] or abs(item["z"]) > 1e-10:
                omissions["nonplanar_or_ocs_curve"] += 1
                return
            available = min(
                int(cfg["max_path_points"]),
                int(cfg["max_geometry_points"]) - geometry_points,
            )
            try:
                geometry = transform_curve(item, transform, available)
            except GeometryBudgetError:
                omissions["curve_point_budget"] += 1
                return
            except ValueError as exc:
                raise SnapshotError(str(exc)) from exc
            geometry_points += len(geometry["points"])
            put(
                placed_paths,
                {
                    "kind": kind,
                    "handle": item["handle"],
                    "source_handle": source_handle,
                    **common,
                    "block_path": list(path_names),
                    "locator": loc,
                    "native": {
                        k: v
                        for k, v in item.items()
                        if k
                        not in {
                            "kind",
                            "handle",
                            "layer",
                            "layout",
                            "paper_space",
                            "unsupported_extrusion",
                        }
                    },
                    "placement_matrix": list(transform),
                    **geometry,
                },
            )
        elif kind == "INSERT":
            name = item["block"]
            if item["paper_space"]:
                omissions["paperspace_insert"] += 1
                return
            if item["unsupported_extrusion"]:
                omissions["unsupported_insert_extrusion"] += 1
                return
            if item["unsupported_z_transform"]:
                omissions["unsupported_z_transform"] += 1
                return
            if item["columns"] != 1 or item["rows"] != 1:
                omissions["insert_array"] += 1
                return
            if name not in blocks:
                omissions["missing_block"] += 1
                return
            b = blocks[name]
            if b["flags"] & (4 | 8):
                omissions["xref_or_overlay_block"] += 1
                return
            if name in path_names:
                omissions["block_cycle"] += 1
                warnings.append(f"block cycle at {name}")
                return
            if len(path_names) >= int(cfg["max_insert_depth"]):
                omissions["insert_depth_cap"] += 1
                return
            reachable_blocks.add(name)
            for label, count in b["unsupported_counts"].items():
                reachable_block_unsupported[f"{name}:{label}"] += count
                omissions[f"reachable_block:{label}"] += count
            if b["index_incomplete"]:
                omissions["reachable_block_index_incomplete"] += 1
            if abs(b["base_z"]) > 1e-10:
                omissions["unsupported_block_base_z"] += 1
                return
            matrix = _compose(transform, _insert_matrix(item, tuple(b["base"])))
            insert_x, insert_y = _point(transform, item["x"], item["y"])
            here = {
                "kind": "INSERT",
                "block": name,
                "x": insert_x,
                "y": insert_y,
                "block_origin": [matrix[4], matrix[5]],
                "handle": item["handle"],
                "source_handle": source_handle,
                **common,
                "block_path": [*path_names, name],
                "locator": loc,
            }
            put(placed_inserts, here)
            for child in b["items"]:
                walk(
                    child,
                    matrix,
                    (*path_names, name),
                    item["handle"],
                    loc + "/" + (child["handle"] or child["kind"]),
                    effective_layer,
                    root_context,
                    common["invisible"],
                )

    for item in entities:
        if item["paper_space"]:
            non_model_counts[item["kind"]] += 1
            continue
        if item["kind"] == "INSERT" and item["unsupported_extrusion"]:
            omissions["unsupported_insert_extrusion"] += 1
            continue
        if item["kind"] == "INSERT" and item["unsupported_z_transform"]:
            omissions["unsupported_z_transform"] += 1
            continue
        if item["kind"] == "INSERT" and (item["columns"] != 1 or item["rows"] != 1):
            omissions["insert_array"] += 1
            continue
        root_context = {
            "handle": item["handle"],
            "layout": item["layout"] or "Model",
            "location": [item["x"], item["y"]] if item["kind"] == "INSERT" else None,
        }
        walk(
            item,
            IDENTITY,
            (),
            item["handle"],
            item["handle"] or item["kind"],
            item["layer"],
            root_context,
        )

    if len(entities) >= int(cfg["max_records_per_scope"]):
        warnings.append("top-level entity cap reached")
    if _stat_signature(source.stat()) != initial_signature:
        raise SnapshotError("source changed while the snapshot was being read")
    unused_block_unsupported: Counter[str] = Counter()
    for name, definition in blocks.items():
        if name not in reachable_blocks:
            for label, count in definition["unsupported_counts"].items():
                unused_block_unsupported[f"{name}:{label}"] += count
            if definition["index_incomplete"]:
                unused_block_unsupported[f"{name}:index_incomplete"] += 1
    # This describes supported CAD records in model-space scope only. It is
    # deliberately independent of device inventory and electrical validation.
    coverage_complete = not omissions and eof_seen and not limits_hit
    result = {
        "format": "rivet-cad-stream-snapshot-v1",
        "source": str(source),
        "source_sha256": digest.hexdigest(),
        "source_bytes": size,
        "parser_version": 3,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "header": {
            "acadver": header.get("$ACADVER"),
            "insunits": header.get("$INSUNITS"),
            "units": units,
        },
        "counts_by_scope_and_type": dict(types),
        "indexed_blocks": len(blocks),
        "indexed_supported_records": supported_seen,
        "coverage_scope": "supported model-space TEXT/MTEXT/ATTRIB/INSERT/LINE/ARC/CIRCLE/LWPOLYLINE records; sampled curves retain chord-error bounds",
        "placed_text": placed_text,
        "placed_lines": placed_lines,
        "placed_inserts": placed_inserts,
        "placed_paths": placed_paths,
        "geometry_points": geometry_points,
        "omissions": dict(omissions),
        "unsupported_counts_by_scope": dict(unsupported_by_scope),
        "non_model_entity_counts": dict(non_model_counts),
        "reachable_block_unsupported_counts": dict(reachable_block_unsupported),
        "unused_block_unsupported_counts": dict(unused_block_unsupported),
        "warnings": sorted(set(warnings)),
        "coverage_complete": coverage_complete,
        "device_inventory_verified": False,
        "electrical_connectivity_verified": False,
        "engineering_validation": "not_performed",
    }
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > int(cfg["max_output_bytes"]):
        raise SnapshotError("snapshot exceeds max_output_bytes; lower record cap")
    return result


def write_snapshot(
    source: str | Path,
    output_dir: str | Path,
    limits: dict[str, float | int] | None = None,
) -> Path:
    """Write a private snapshot into a newly created output directory."""
    outdir = Path(output_dir)
    result = snapshot_dxf(source, limits)
    outdir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(outdir, 0o700)
    target = outdir / "snapshot.json"
    with target.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    os.chmod(target, 0o600)
    return target
