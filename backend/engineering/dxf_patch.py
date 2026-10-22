"""Byte-preserving, handle-targeted edits for ASCII DXF text entities.

The module deliberately does not use ezdxf: the source is walked as raw
group-code/value pairs and only the text value pairs of requested entities are
rewritten.  All other source bytes are copied verbatim.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DxfPatchError(ValueError):
    """A requested edit cannot be applied without risking the drawing."""


_TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB"}
_MAX_LINE = 1_000_000
_HEADER_READ = 65536
_MAX_WANTED_HANDLES = 500
_MAX_SCANNED_RECORDS = 5_000_000
_MAX_ENTITY_PAIRS = 2_000
_MAX_ENTITY_CHARS = 2_000_000
_MAX_TEXT_CHARS = 1_000_000
_MAX_RETAINED_PAIRS = 100_000
_MAX_RETAINED_CHARS = 20_000_000
UNICODE_ESCAPE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


@dataclass(frozen=True)
class _Pair:
    code: bytes
    value: bytes
    start: int
    end: int
    newline: bytes
    number: int


@dataclass
class _Entity:
    kind: str
    handle: str
    scope: str
    pairs: list[_Pair]


@dataclass(frozen=True)
class _Target:
    edit: Mapping[str, Any]
    entity: _Entity
    text: str
    patch_start: int
    patch_end: int
    replacement_pairs: bytes


def _decode_unicode_escapes(text: str) -> str:
    def convert_pair(match: re.Match[str]) -> str:
        high, low = int(match.group(1), 16), int(match.group(2), 16)
        return chr(0x10000 + ((high - 0xD800) << 10) + low - 0xDC00)

    text = re.sub(
        r"\\U\+(D[89ABab][0-9A-Fa-f]{2})\\U\+(D[C-Fc-f][0-9A-Fa-f]{2})",
        convert_pair,
        text,
    )

    def convert(match: re.Match[str]) -> str:
        value = int(match.group(1), 16)
        return match.group(0) if 0xD800 <= value <= 0xDFFF else chr(value)

    return UNICODE_ESCAPE.sub(convert, text)


def _split_line(line: bytes) -> tuple[bytes, bytes]:
    if line.endswith(b"\r\n"):
        return line[:-2], b"\r\n"
    if line.endswith((b"\n", b"\r")):
        return line[:-1], line[-1:]
    return line, b""


def _encoding(path: Path) -> tuple[str, bool]:
    with path.open("rb") as stream:
        prefix = stream.read(_HEADER_READ)
    if prefix.startswith(b"AutoCAD Binary DXF"):
        raise DxfPatchError("Binary DXF is unsupported; export an ASCII DXF")
    version = re.search(rb"\$ACADVER\s*\r?\n\s*1\s*\r?\n(AC\d+)", prefix)
    codepage = re.search(rb"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n([^\r\n]+)", prefix)
    modern = bool(version and int(version[1][2:]) >= 1021)
    cp = codepage[1].strip().upper() if codepage else b""
    if cp and cp not in {b"ANSI_1252", b"UTF-8", b"UTF8"}:
        raise DxfPatchError("Unsupported DXF code page; export ANSI_1252 or UTF-8 DXF")
    return ("utf-8" if modern or cp in {b"UTF-8", b"UTF8"} else "cp1252", modern)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_stream_range(stream: Any, digest: Any, size: int) -> None:
    remaining = size
    while remaining:
        requested = min(1024 * 1024, remaining)
        chunk = stream.read(requested)
        if len(chunk) != requested:
            raise DxfPatchError("short read during untouched-byte verification")
        digest.update(chunk)
        remaining -= len(chunk)


def _read_pairs(stream: Any) -> Iterable[_Pair]:
    while True:
        start = stream.tell()
        code_line = stream.readline(_MAX_LINE + 2)
        if not code_line:
            return
        if len(code_line) > _MAX_LINE + 1:
            raise DxfPatchError("DXF group-code line exceeds the supported limit")
        code_raw, code_nl = _split_line(code_line)
        value_line = stream.readline(_MAX_LINE + 2)
        if not value_line:
            raise DxfPatchError("unterminated DXF group-code/value pair")
        if len(value_line) > _MAX_LINE + 1:
            raise DxfPatchError("DXF value line exceeds the supported limit")
        _value_raw, value_nl = _split_line(value_line)
        try:
            number = int(code_raw.strip())
        except ValueError as exc:
            raise DxfPatchError(f"invalid DXF group code {code_raw[:40]!r}") from exc
        yield _Pair(
            code_line, value_line, start, stream.tell(), value_nl or code_nl, number
        )


def _text_pairs(entity: _Entity) -> list[_Pair]:
    if entity.kind == "MTEXT":
        return [pair for pair in entity.pairs if pair.number in (1, 3)]
    return [pair for pair in entity.pairs if pair.number == 1]


def _decode_text(pairs: list[_Pair], encoding: str) -> str:
    try:
        return "".join(
            _split_line(pair.value)[0].decode(encoding, errors="strict")
            for pair in pairs
        )
    except UnicodeDecodeError as exc:
        raise DxfPatchError(
            "text entity contains bytes invalid for its declared DXF encoding"
        ) from exc


def _unicode_escape(text: str) -> str:
    out: list[str] = []
    for char in text:
        value = ord(char)
        if value <= 0xFF:
            out.append(char)
        elif value <= 0xFFFF:
            out.append(f"\\U+{value:04X}")
        else:
            value -= 0x10000
            high, low = 0xD800 + (value >> 10), 0xDC00 + (value & 0x3FF)
            out.extend((f"\\U+{high:04X}", f"\\U+{low:04X}"))
    return "".join(out)


def _encode_replacement(text: str, encoding: str, modern: bool, kind: str) -> bytes:
    for char in text:
        codepoint = ord(char)
        if codepoint < 32 or codepoint == 127:
            raise DxfPatchError(
                "replacement text cannot contain control or newline characters"
            )
    if kind != "MTEXT" and ("\r" in text or "\n" in text):
        raise DxfPatchError("replacement text cannot contain newlines")
    value = text if modern or encoding == "utf-8" else _unicode_escape(text)
    try:
        return value.encode(encoding, errors="strict")
    except UnicodeEncodeError:
        # ANSI_1252 drawings support arbitrary Unicode using DXF escapes.
        return _unicode_escape(text).encode(encoding, errors="strict")


def _replace_text_pairs(
    entity: _Entity,
    text_pairs: list[_Pair],
    replacement: str,
    encoding: str,
    modern: bool,
) -> tuple[int, int, bytes]:
    if not text_pairs:
        raise DxfPatchError(f"{entity.kind} handle {entity.handle} has no text value")
    positions = [entity.pairs.index(pair) for pair in text_pairs]
    if positions != list(range(positions[0], positions[-1] + 1)):
        raise DxfPatchError(
            f"{entity.kind} handle {entity.handle} has interleaved text fields"
        )
    newline = text_pairs[0].newline or b"\n"
    raw = _encode_replacement(replacement, encoding, modern, entity.kind)
    # DXF MTEXT chunks are limited to 250 characters.  Keep chunks shorter
    # (200 UTF-8 bytes max) so they remain valid with multibyte text too.
    if entity.kind == "MTEXT":
        text = raw.decode(encoding)
        chunks: list[bytes] = []
        current = ""
        for char in text:
            trial = current + char
            if current and (len(trial) > 200 or len(trial.encode(encoding)) > 200):
                chunks.append(current.encode(encoding))
                current = char
            else:
                current = trial
        chunks.append(current.encode(encoding))
        if not chunks:
            chunks = [b""]
        pair_bytes = b"".join(
            (b"3" if index < len(chunks) - 1 else b"1") + newline + chunk + newline
            for index, chunk in enumerate(chunks)
        )
    else:
        pair_bytes = b"1" + newline + raw + newline
    return text_pairs[0].start, text_pairs[-1].end, pair_bytes


def _scan(
    path: Path, encoding: str, wanted: set[str]
) -> tuple[list[_Entity], dict[str, int]]:
    if len(wanted) > _MAX_WANTED_HANDLES:
        raise DxfPatchError(f"at most {_MAX_WANTED_HANDLES} handles can be inspected at once")
    entities: list[_Entity] = []
    handle_counts: dict[str, int] = {}
    section = ""
    in_section_name = False
    current: _Entity | None = None
    current_pairs = 0
    current_chars = 0
    retained_pairs = 0
    retained_chars = 0
    scanned_records = 0
    eof_seen = False

    def finish() -> None:
        nonlocal current, current_pairs, current_chars, retained_pairs, retained_chars
        if current is not None:
            if current.handle in wanted:
                handle_counts[current.handle] = handle_counts.get(current.handle, 0) + 1
                if current.kind in _TEXT_TYPES:
                    retained_pairs += current_pairs
                    retained_chars += current_chars
                    if retained_pairs > _MAX_RETAINED_PAIRS:
                        raise DxfPatchError("selected text exceeds the supported retained pair count")
                    if retained_chars > _MAX_RETAINED_CHARS:
                        raise DxfPatchError("selected text exceeds the supported retained character limit")
                    entities.append(current)
            current = None
        current_pairs = 0
        current_chars = 0

    with path.open("rb") as stream:
        for pair in _read_pairs(stream):
            if eof_seen:
                raise DxfPatchError("DXF contains data after the 0/EOF marker")
            value = _split_line(pair.value)[0]
            if pair.number == 0:
                finish()
                scanned_records += 1
                if scanned_records > _MAX_SCANNED_RECORDS:
                    raise DxfPatchError("DXF exceeds the supported record count")
                try:
                    marker = value.decode("ascii").strip().upper()
                except UnicodeDecodeError:
                    marker = ""
                if marker == "SECTION":
                    in_section_name = True
                elif marker == "ENDSEC":
                    section = ""
                    in_section_name = False
                elif marker == "EOF":
                    in_section_name = False
                    eof_seen = True
            elif in_section_name and pair.number == 2:
                section = value.decode(encoding, errors="strict").strip().upper()
                in_section_name = False
            elif pair.number == 0:
                pass
            elif current is not None:
                if current.kind in _TEXT_TYPES:
                    current.pairs.append(pair)
                    current_pairs += 1
                    current_chars += len(pair.code) + len(pair.value)
                    if current_pairs > _MAX_ENTITY_PAIRS:
                        raise DxfPatchError("text entity exceeds the supported pair count")
                    if current_chars > _MAX_ENTITY_CHARS:
                        raise DxfPatchError("text entity exceeds the supported character limit")
                if pair.number == 5 and not current.handle:
                    current.handle = (
                        value.decode("ascii", errors="ignore").strip().upper()
                    )
            if pair.number == 0:
                try:
                    kind = value.decode("ascii").strip().upper()
                except UnicodeDecodeError:
                    kind = ""
                if kind:
                    current = _Entity(kind=kind, handle="", scope=section, pairs=[pair])
                    if kind in _TEXT_TYPES:
                        current_pairs = 1
                        current_chars = len(pair.code) + len(pair.value)
        finish()
    if not eof_seen:
        raise DxfPatchError("DXF is missing the required 0/EOF marker")
    return entities, handle_counts


def _edits_by_handle(
    edits: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    ids: set[str] = set()
    for edit in edits:
        if not isinstance(edit, Mapping):
            raise DxfPatchError("each edit must be a mapping")
        for field in (
            "id",
            "handle",
            "entity_type",
            "expected_text",
            "replacement_text",
        ):
            if field not in edit:
                raise DxfPatchError(f"edit is missing {field!r}")
        edit_id = str(edit["id"])
        handle = str(edit["handle"]).strip().upper()
        if not handle:
            raise DxfPatchError("edit handle cannot be empty")
        if edit_id in ids:
            raise DxfPatchError(f"duplicate edit id {edit_id!r}")
        if handle in result:
            raise DxfPatchError(f"duplicate edit handle {handle!r}")
        if str(edit["entity_type"]).upper() not in _TEXT_TYPES:
            raise DxfPatchError("only TEXT, MTEXT, and ATTRIB entities can be edited")
        if not isinstance(edit["expected_text"], str) or not isinstance(
            edit["replacement_text"], str
        ):
            raise DxfPatchError("expected_text and replacement_text must be strings")
        if len(edit["expected_text"]) > _MAX_TEXT_CHARS:
            raise DxfPatchError("expected_text exceeds the supported character limit")
        if len(edit["replacement_text"]) > _MAX_TEXT_CHARS:
            raise DxfPatchError("replacement_text exceeds the supported character limit")
        ids.add(edit_id)
        result[handle] = edit
        if len(result) > _MAX_WANTED_HANDLES:
            raise DxfPatchError(f"at most {_MAX_WANTED_HANDLES} handles can be patched at once")
    return result


def _read_targets(
    source: Path, edits: dict[str, Mapping[str, Any]], encoding: str, modern: bool
) -> list[_Target]:
    entities, counts = _scan(source, encoding, set(edits))
    candidates: dict[str, list[_Entity]] = {}
    for entity in entities:
        if entity.handle in edits:
            candidates.setdefault(entity.handle, []).append(entity)
    targets: list[_Target] = []
    for handle, edit in edits.items():
        matches = candidates.get(handle, [])
        if not matches:
            raise DxfPatchError(f"target handle {handle} was not found")
        if counts.get(handle, 0) > 1:
            raise DxfPatchError(f"target handle {handle} appears more than once")
        entity = matches[0]
        requested_type = str(edit["entity_type"]).upper()
        if entity.kind != requested_type:
            raise DxfPatchError(
                f"handle {handle} is {entity.kind}, expected {requested_type}"
            )
        if entity.scope == "BLOCKS" and not edit.get("allow_shared_definition", False):
            raise DxfPatchError(f"handle {handle} is in a shared BLOCKS definition")
        parts = _text_pairs(entity)
        before = _decode_text(parts, encoding)
        if before != edit["expected_text"]:
            raise DxfPatchError(
                f"handle {handle} text does not match expected_text exactly"
            )
        start, end, replacement_pairs = _replace_text_pairs(
            entity, parts, edit["replacement_text"], encoding, modern
        )
        targets.append(_Target(edit, entity, before, start, end, replacement_pairs))
    return sorted(targets, key=lambda target: target.patch_start)


def _copy_with_patches(
    source: Path, temporary: Path, targets: list[_Target]
) -> tuple[str, str]:
    source_digest = hashlib.sha256()
    untouched_digest = hashlib.sha256()
    patch_by_start = {target.patch_start: target for target in targets}
    starts = sorted(patch_by_start)
    with source.open("rb") as src, temporary.open("wb") as dst:
        cursor = 0
        for start in starts:
            if start < cursor:
                raise DxfPatchError("requested text fields overlap")
            while cursor < start:
                data = src.read(min(1024 * 1024, start - cursor))
                if not data:
                    raise DxfPatchError(
                        "source ended while copying to temporary output"
                    )
                dst.write(data)
                source_digest.update(data)
                untouched_digest.update(data)
                cursor += len(data)
            target = patch_by_start[start]
            old_size = target.patch_end - target.patch_start
            old = src.read(old_size)
            if len(old) != old_size:
                raise DxfPatchError("source ended inside a target text field")
            source_digest.update(old)
            dst.write(target.replacement_pairs)
            cursor += old_size
        for data in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(data)
            source_digest.update(data)
            untouched_digest.update(data)
    return source_digest.hexdigest(), untouched_digest.hexdigest()


def inspect_text_records(
    source: str | Path, handles: Iterable[str]
) -> list[dict[str, Any]]:
    """Return bounded text metadata for selected handles in an ASCII DXF."""
    path = Path(source)
    if not path.is_file():
        raise DxfPatchError(f"not a regular input file: {path}")
    encoding, _ = _encoding(path)
    selected: set[str] = set()
    for handle in handles:
        selected.add(str(handle).strip().upper())
        if len(selected) > _MAX_WANTED_HANDLES:
            raise DxfPatchError(
                f"at most {_MAX_WANTED_HANDLES} handles can be inspected at once"
            )
    entities, counts = _scan(path, encoding, selected)
    found: dict[str, list[_Entity]] = {}
    for entity in entities:
        if entity.handle in selected:
            found.setdefault(entity.handle, []).append(entity)
    results: list[dict[str, Any]] = []
    for handle in selected:
        matches = found.get(handle, [])
        if counts.get(handle, 0) > 1:
            raise DxfPatchError(f"handle {handle} appears more than once")
        if not matches:
            continue
        entity = matches[0]
        values: dict[int, list[str]] = {}
        for pair in entity.pairs:
            if pair.number in {8, 10, 20, 40, 41, 50}:
                values.setdefault(pair.number, []).append(
                    _split_line(pair.value)[0].decode(encoding, errors="strict")
                )
        results.append(
            {
                "handle": handle,
                "entity_type": entity.kind,
                "text": _decode_text(_text_pairs(entity), encoding),
                "layer": values.get(8, [""])[0],
                "x": values.get(10, [""])[0],
                "y": values.get(20, [""])[0],
                "height": values.get(40, [""])[0],
                "width": values.get(41, [""])[0],
                "rotation": values.get(50, [""])[0],
                "scope": entity.scope,
            }
        )
    return results


def patch_dxf(
    source: str | Path,
    output: str | Path,
    edits: Iterable[Mapping[str, Any]],
    expected_source_sha256: str,
) -> dict[str, Any]:
    """Patch selected text entities and atomically create a new DXF file.

    The output must not already exist. The original is never modified.
    """
    source_path, output_path = Path(source), Path(output)
    if not source_path.is_file():
        raise DxfPatchError(f"not a regular input file: {source_path}")
    if source_path.resolve() == output_path.resolve() or (
        output_path.exists() and os.path.samefile(source_path, output_path)
    ):
        raise DxfPatchError("source and output paths must be different")
    if output_path.exists():
        raise DxfPatchError(f"output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    edit_map = _edits_by_handle(edits)
    before_hash = _sha256(source_path)
    if before_hash.lower() != str(expected_source_sha256).lower():
        raise DxfPatchError("source SHA-256 does not match expected_source_sha256")
    encoding, modern = _encoding(source_path)
    targets = _read_targets(source_path, edit_map, encoding, modern)
    if not targets:
        raise DxfPatchError("at least one text edit is required")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    os.close(fd)
    temporary = Path(temp_name)
    try:
        written_source_hash, untouched_before = _copy_with_patches(
            source_path, temporary, targets
        )
        if written_source_hash != before_hash or _sha256(source_path) != before_hash:
            raise DxfPatchError("source changed while the patch was being prepared")
        if _sha256(temporary) == before_hash:
            raise DxfPatchError("patch did not change the output")
        verify_entities, _ = _scan(temporary, encoding, set(edit_map))
        verify_by_handle: dict[str, list[_Entity]] = {}
        for entity in verify_entities:
            if entity.handle in edit_map:
                verify_by_handle.setdefault(entity.handle, []).append(entity)
        for target in targets:
            matches = verify_by_handle.get(target.entity.handle, [])
            actual = (
                _decode_text(_text_pairs(matches[0]), encoding)
                if len(matches) == 1
                else None
            )
            if (
                len(matches) != 1
                or _decode_unicode_escapes(actual or "")
                != _decode_unicode_escapes(target.edit["replacement_text"])
            ):
                raise DxfPatchError(
                    f"output verification failed for handle {target.entity.handle}"
                )
        # Independent byte check: remove only the rewritten byte spans from the
        # source and corresponding replacement spans from output.
        output_untouched = hashlib.sha256()
        with temporary.open("rb") as stream:
            cursor = 0
            output_cursor = 0
            for target in targets:
                untouched_size = target.patch_start - cursor
                stream.seek(output_cursor)
                _hash_stream_range(stream, output_untouched, untouched_size)
                output_cursor += untouched_size + len(target.replacement_pairs)
                cursor = target.patch_end
            stream.seek(output_cursor)
            output_size = temporary.stat().st_size
            if output_cursor > output_size:
                raise DxfPatchError("output ended before untouched-byte verification completed")
            _hash_stream_range(stream, output_untouched, output_size - output_cursor)
        if output_untouched.hexdigest() != untouched_before:
            raise DxfPatchError("output contains changes outside requested text values")
        output_hash = _sha256(temporary)
        # Hard-link gives an atomic no-overwrite publication on the same volume.
        os.link(temporary, output_path)
        temporary.unlink()
        return {
            "source_sha256": before_hash,
            "output_sha256": output_hash,
            "applied": [
                {
                    "id": str(target.edit["id"]),
                    "handle": target.entity.handle,
                    "before": target.text,
                    "after": str(target.edit["replacement_text"]),
                }
                for target in targets
            ],
            "preservation_verified": True,
            "untouched_bytes_sha256": untouched_before,
        }
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
