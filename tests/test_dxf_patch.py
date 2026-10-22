"""Pure fixture tests for surgical ASCII DXF text patching."""

import hashlib

import pytest

from backend.engineering import dxf_patch
from backend.engineering.dxf_patch import DxfPatchError, inspect_text_records, patch_dxf


@pytest.fixture(autouse=True)
def database():
    """These byte-level tests do not need the application database."""
    yield


def _pair(code, value, nl=b"\n"):
    return str(code).encode() + nl + value + nl


def _drawing(*, modern=False, duplicate=False, block=False, newline=b"\r\n"):
    version = b"AC1027" if modern else b"AC1015"
    data = b"".join(
        [
            _pair(0, b"SECTION", newline),
            _pair(2, b"HEADER", newline),
            _pair(9, b"$ACADVER", newline),
            _pair(1, version, newline),
            _pair(9, b"$DWGCODEPAGE", newline),
            _pair(3, b"ANSI_1252", newline),
            _pair(0, b"ENDSEC", newline),
            _pair(0, b"SECTION", newline),
            _pair(2, b"BLOCKS" if block else b"ENTITIES", newline),
            _pair(0, b"TEXT", newline),
            _pair(5, b"A1", newline),
            _pair(8, b"LABELS", newline),
            _pair(10, b"12.5", newline),
            _pair(20, b"-4", newline),
            _pair(40, b"2.5", newline),
            _pair(1, b"Old label", newline),
        ]
    )
    if duplicate:
        data += _pair(0, b"LINE", newline) + _pair(5, b"A1", newline) + _pair(8, b"0", newline)
    if not block:
        data += b"".join(
            [
                _pair(0, b"MTEXT", newline),
                _pair(5, b"B2", newline),
                _pair(8, b"NOTES", newline),
                _pair(3, b"First chunk ", newline),
                _pair(3, b"second ", newline),
                _pair(1, b"part", newline),
            ]
        )
    data += _pair(0, b"ENDSEC", newline) + _pair(0, b"EOF", newline)
    return data


def _edit(handle="A1", *, kind="TEXT", before="Old label", after="New label", **extra):
    return {
        "id": f"edit-{handle}",
        "handle": handle,
        "entity_type": kind,
        "expected_text": before,
        "replacement_text": after,
        **extra,
    }


def _patch(source, output, edits):
    return patch_dxf(source, output, edits, hashlib.sha256(source.read_bytes()).hexdigest())


def test_text_patch_preserves_all_other_bytes_and_reports_hashes(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    original = _drawing()
    source.write_bytes(original)

    result = _patch(source, output, [_edit()])

    expected = original.replace(_pair(1, b"Old label", b"\r\n"), _pair(1, b"New label", b"\r\n"))
    assert output.read_bytes() == expected
    assert source.read_bytes() == original
    assert result["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["output_sha256"] == hashlib.sha256(expected).hexdigest()
    assert result["preservation_verified"] is True
    assert result["applied"] == [{"id": "edit-A1", "handle": "A1", "before": "Old label", "after": "New label"}]
    assert inspect_text_records(output, ["A1"])[0]["text"] == "New label"


def test_mtext_rechunks_text_and_keeps_paragraph_marker(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing())
    after = ("x" * 235) + r"\P" + ("y" * 240)

    _patch(source, output, [_edit("B2", kind="MTEXT", before="First chunk second part", after=after)])

    records = inspect_text_records(output, ["B2"])
    assert records[0]["text"] == after
    raw = output.read_bytes()
    assert raw.count(b"\r\n3\r\n") >= 2
    assert b"\\P" in raw


def test_refuses_hash_mismatch_missing_wrong_type_and_duplicate_handles(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing())
    with pytest.raises(DxfPatchError, match="SHA-256"):
        patch_dxf(source, output, [_edit()], "0" * 64)
    with pytest.raises(DxfPatchError, match="not found"):
        _patch(source, output, [_edit("NOPE")])
    with pytest.raises(DxfPatchError, match="expected MTEXT"):
        _patch(source, output, [_edit(kind="MTEXT")])
    source.write_bytes(_drawing(duplicate=True))
    with pytest.raises(DxfPatchError, match="appears more than once"):
        _patch(source, output, [_edit()])
    assert not output.exists()


def test_source_and_output_must_have_terminal_eof_marker(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    valid = _drawing()
    source.write_bytes(valid.removesuffix(_pair(0, b"EOF", b"\r\n")))
    with pytest.raises(DxfPatchError, match="missing the required 0/EOF"):
        _patch(source, output, [_edit()])
    source.write_bytes(valid + _pair(999, b"trailing comment", b"\r\n"))
    with pytest.raises(DxfPatchError, match="data after the 0/EOF"):
        _patch(source, output, [_edit()])
    assert not output.exists()


def test_source_change_during_copy_is_detected_and_temp_output_is_removed(tmp_path, monkeypatch):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing())
    original_copy = dxf_patch._copy_with_patches

    def copy_then_change(path, temporary, targets):
        result = original_copy(path, temporary, targets)
        with path.open("ab") as stream:
            stream.write(b"changed")
        return result

    monkeypatch.setattr(dxf_patch, "_copy_with_patches", copy_then_change)
    with pytest.raises(DxfPatchError, match="source changed"):
        _patch(source, output, [_edit()])
    assert not output.exists()
    assert list(tmp_path.glob(".output.dxf.*.tmp")) == []


def test_rejects_shared_block_definition_unless_explicitly_allowed(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing(block=True))
    with pytest.raises(DxfPatchError, match="shared BLOCKS"):
        _patch(source, output, [_edit()])
    _patch(source, output, [_edit(allow_shared_definition=True)])


def test_legacy_unicode_is_escaped_and_modern_unicode_is_utf8(tmp_path):
    legacy, legacy_out = tmp_path / "legacy.dxf", tmp_path / "legacy-out.dxf"
    legacy.write_bytes(_drawing())
    _patch(legacy, legacy_out, [_edit(after="Panel Ω 😀")])
    raw_legacy = legacy_out.read_bytes()
    assert b"Panel \\U+03A9 \\U+D83D\\U+DE00" in raw_legacy
    assert inspect_text_records(legacy_out, ["A1"])[0]["text"] == r"Panel \U+03A9 \U+D83D\U+DE00"

    modern, modern_out = tmp_path / "modern.dxf", tmp_path / "modern-out.dxf"
    modern.write_bytes(_drawing(modern=True))
    _patch(modern, modern_out, [_edit(after="Panel Ω 😀")])
    assert "Panel Ω 😀".encode() in modern_out.read_bytes()
    assert inspect_text_records(modern_out, ["A1"])[0]["text"] == "Panel Ω 😀"


def test_legacy_replacement_can_keep_existing_raw_unicode_escape(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing().replace(b"Old label", b"\\U+03A9"))

    _patch(source, output, [_edit(before=r"\U+03A9", after=r"\U+03A9 label")])

    assert b"\\U+03A9 label" in output.read_bytes()
    assert inspect_text_records(output, ["A1"])[0]["text"] == r"\U+03A9 label"


def test_refuses_newlines_controls_and_existing_output(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing())
    with pytest.raises(DxfPatchError, match="control or newline"):
        _patch(source, output, [_edit(after="two\nlines")])
    output.write_bytes(b"keep me")
    with pytest.raises(DxfPatchError, match="already exists"):
        _patch(source, output, [_edit()])
    assert output.read_bytes() == b"keep me"


def test_duplicate_edit_handle_and_text_expectation_are_refused(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing())
    with pytest.raises(DxfPatchError, match="duplicate edit handle"):
        _patch(source, output, [_edit(), _edit(id="other-edit", after="Other")])
    with pytest.raises(DxfPatchError, match="does not match expected_text"):
        _patch(source, output, [_edit(before="stale")])


def test_caps_inspected_handles_and_expected_text_size(tmp_path):
    source, output = tmp_path / "source.dxf", tmp_path / "output.dxf"
    source.write_bytes(_drawing())
    with pytest.raises(DxfPatchError, match="at most 500 handles"):
        inspect_text_records(source, (str(index) for index in range(501)))
    with pytest.raises(DxfPatchError, match="expected_text exceeds"):
        _patch(source, output, [_edit(before="x" * 1_000_001)])
