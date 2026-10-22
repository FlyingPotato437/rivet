from __future__ import annotations

import json

import pytest

from backend.engineering import cad_snapshot
from backend.engineering.cad_snapshot import SnapshotError, snapshot_dxf, write_snapshot


@pytest.fixture(autouse=True)
def database():
    """Override the repository DB-reset fixture; these tests are pure parsing."""
    yield


def dxf(tmp_path, content: str):
    p = tmp_path / "sample.dxf"
    p.write_text(content, encoding="cp1252")
    return p


def test_nested_insert_transform_basepoint_and_unused_text(tmp_path):
    p = dxf(
        tmp_path,
        r"""0
SECTION
2
HEADER
9
$INSUNITS
70
1
0
ENDSEC
0
SECTION
2
BLOCKS
0
BLOCK
5
B1
2
B1
10
10
20
0
0
TEXT
5
T1
8
0
10
10
20
2
1
placed \U+4E2D
0
INSERT
5
I2
2
B2
10
2
20
0
41
1
42
1
0
ENDBLK
0
BLOCK
5
B2H
2
B2
10
1
20
0
0
LINE
5
L2
10
1
20
0
11
3
21
0
0
ENDBLK
0
BLOCK
5
UNUSED
2
UNUSED
0
TEXT
5
TU
10
0
20
0
1
never place me
0
ENDBLK
0
ENDSEC
0
SECTION
2
ENTITIES
0
INSERT
5
I1
2
B1
8
signal
10
100
20
50
41
2
42
2
50
90
0
ENDSEC
0
EOF
""",
    )
    out = snapshot_dxf(p)
    assert out["header"]["units"] == "inches"
    assert len(out["placed_text"]) == 1
    assert out["placed_text"][0]["text"] == "placed 中"
    assert out["placed_text"][0]["block_path"] == ["B1"]
    assert out["placed_text"][0]["layer"] == "signal"
    assert out["placed_text"][0]["layout"] == "Model"
    assert out["placed_text"][0]["root_handle"] == "I1"
    assert out["placed_text"][0]["root_location"] == [100.0, 50.0]
    assert len(out["placed_lines"]) == 1
    line = out["placed_lines"][0]
    assert line["start"][:2] == pytest.approx((100, 34))
    assert line["end"][:2] == pytest.approx((100, 38))
    assert line["layer"] == "signal"
    assert out["placed_inserts"][0]["x"] == pytest.approx(100)
    assert out["placed_inserts"][0]["y"] == pytest.approx(50)
    assert out["placed_inserts"][1]["x"] == pytest.approx(100)
    assert out["placed_inserts"][1]["y"] == pytest.approx(34)
    assert "never place me" not in json.dumps(out)


def test_cycle_arrays_and_missing_block_are_omitted(tmp_path):
    p = dxf(
        tmp_path,
        r"""0
SECTION
2
BLOCKS
0
BLOCK
5
BH
2
A
0
INSERT
5
IA
2
A
10
0
20
0
0
ENDBLK
0
ENDSEC
0
SECTION
2
ENTITIES
0
INSERT
5
I1
2
A
10
0
20
0
0
INSERT
5
I2
2
MISSING
10
0
20
0
0
INSERT
5
I3
2
A
10
0
20
0
70
2
0
ENDSEC
0
EOF
""",
    )
    out = snapshot_dxf(p)
    assert out["omissions"]["block_cycle"] == 1
    assert out["omissions"]["missing_block"] == 1
    assert out["omissions"]["insert_array"] == 1
    assert out["coverage_complete"] is False


def test_caps_and_nondefault_extrusion_are_reported(tmp_path):
    p = dxf(
        tmp_path,
        r"""0
SECTION
2
ENTITIES
0
TEXT
5
T1
10
1
20
2
1
A
0
TEXT
5
T2
10
3
20
4
1
B
0
INSERT
5
I
2
NO
210
1
0
ENDSEC
0
EOF
""",
    )
    out = snapshot_dxf(p, {"max_supported_records": 1})
    assert len(out["placed_text"]) == 1
    assert out["omissions"]["located_output_cap"] >= 1
    assert out["omissions"]["unsupported_insert_extrusion"] == 1
    assert not out["coverage_complete"]


def test_unterminated_pair_fails_closed_and_output_is_private_new_dir(tmp_path):
    p = tmp_path / "bad.dxf"
    p.write_text("0\nSECTION\n2\nENTITIES\n0\nTEXT\n1", encoding="ascii")
    with pytest.raises(SnapshotError, match="unterminated"):
        snapshot_dxf(p)
    good = dxf(tmp_path, "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    outdir = tmp_path / "private"
    written = write_snapshot(good, outdir)
    assert outdir.stat().st_mode & 0o777 == 0o700
    assert written.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_snapshot(good, outdir)


def test_oversized_polyline_is_skipped_and_paperspace_is_not_located(tmp_path):
    p = dxf(
        tmp_path,
        """0
SECTION
2
ENTITIES
0
LWPOLYLINE
5
P1
10
1
20
2
10
3
20
4
10
5
20
6
0
TEXT
5
T1
410
Layout1
10
0
20
0
1
paper text
0
TEXT
5
T2
10
0
20
0
1
model text
0
ENDSEC
0
EOF
""",
    )
    out = snapshot_dxf(p, {"max_record_pairs": 5})
    assert out["omissions"]["oversized_record:ENTITIES:LWPOLYLINE"] == 1
    assert out["non_model_entity_counts"]["TEXT"] == 1
    assert [item["text"] for item in out["placed_text"]] == ["model text"]
    assert out["coverage_complete"] is False


def test_unused_unsupported_block_and_metadata_do_not_break_model_coverage(tmp_path):
    p = dxf(
        tmp_path,
        """0
SECTION
2
TABLES
0
TABLE
2
LAYER
0
LAYER
2
visible
70
0
0
ENDTAB
0
ENDSEC
0
SECTION
2
BLOCKS
0
BLOCK
5
BU
2
UNUSED
0
LWPOLYLINE
5
P1
90
3
10
0
20
0
0
ENDBLK
0
ENDSEC
0
SECTION
2
ENTITIES
0
TEXT
5
T1
10
1
20
2
1
model text
0
ENDSEC
0
SECTION
2
OBJECTS
0
DICTIONARY
5
D1
0
ENDSEC
0
EOF
""",
    )
    out = snapshot_dxf(p)
    assert out["coverage_complete"] is True
    assert out["omissions"] == {}
    assert out["unsupported_counts_by_scope"]["TABLES:LAYER"] == 1
    assert out["unsupported_counts_by_scope"]["OBJECTS:DICTIONARY"] == 1
    assert out["unused_block_unsupported_counts"]["UNUSED:unsupported:LWPOLYLINE"] == 1
    assert out["device_inventory_verified"] is False
    assert out["electrical_connectivity_verified"] is False
    assert out["engineering_validation"] == "not_performed"


def test_text_ocs_and_block_base_z_are_rejected(tmp_path):
    p = dxf(
        tmp_path,
        """0
SECTION
2
BLOCKS
0
BLOCK
5
BH
2
ZBLOCK
10
0
20
0
30
3
0
LINE
5
L1
10
0
20
0
11
1
21
0
0
ENDBLK
0
ENDSEC
0
SECTION
2
ENTITIES
0
INSERT
5
I1
2
ZBLOCK
10
0
20
0
0
TEXT
5
T1
10
0
20
0
210
1
1
ocs text
0
ENDSEC
0
EOF
""",
    )
    out = snapshot_dxf(p)
    assert out["omissions"]["unsupported_block_base_z"] == 1
    assert out["omissions"]["unsupported_text_extrusion"] == 1
    assert out["placed_lines"] == []
    assert out["placed_text"] == []
    assert out["coverage_complete"] is False


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_invalid_numeric_values_and_limits_fail_closed(tmp_path, value):
    p = dxf(
        tmp_path,
        """0
SECTION
2
ENTITIES
0
TEXT
5
T1
10
nan
20
2
1
x
0
ENDSEC
0
EOF
""",
    )
    with pytest.raises(SnapshotError, match="numeric"):
        snapshot_dxf(p)
    with pytest.raises(SnapshotError, match="limit"):
        snapshot_dxf(
            dxf(tmp_path, "0\nSECTION\n2\nENTITIES\n0\nEOF\n"), {"max_seconds": value}
        )


def test_nonfinite_coordinates_and_unbounded_value_lines_are_rejected(tmp_path):
    p = dxf(
        tmp_path,
        """0
SECTION
2
ENTITIES
0
LINE
5
L1
10
NaN
20
0
11
1
21
1
0
ENDSEC
0
EOF
""",
    )
    with pytest.raises(SnapshotError, match="non-finite"):
        snapshot_dxf(p)
    huge = dxf(
        tmp_path, "0\nSECTION\n2\nENTITIES\n0\nTEXT\n1\n" + "x" * 100 + "\n0\nEOF\n"
    )
    with pytest.raises(SnapshotError, match="max_line_chars"):
        snapshot_dxf(huge, {"max_line_chars": 32})


def test_source_change_during_parse_is_detected(tmp_path, monkeypatch):
    p = dxf(tmp_path, "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    original_pairs = cad_snapshot._pairs

    def mutate_before_eof(path, max_line_chars):
        for pair in original_pairs(path, max_line_chars):
            if pair == ("0", "EOF"):
                path.write_text(
                    path.read_text(encoding="cp1252") + "\n", encoding="cp1252"
                )
            yield pair

    monkeypatch.setattr(cad_snapshot, "_pairs", mutate_before_eof)
    with pytest.raises(SnapshotError, match="source changed"):
        snapshot_dxf(p)


def test_modern_utf8_and_legacy_escaped_labels_preserve_their_text(tmp_path):
    modern = tmp_path / "modern.dxf"
    modern.write_text(
        "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1027\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nTEXT\n5\nA1\n10\n0\n20\n0\n1\n变压器 B\n0\nENDSEC\n0\nEOF\n",
        encoding="utf-8",
    )
    assert snapshot_dxf(modern)["placed_text"][0]["text"] == "变压器 B"
    assert cad_snapshot._decode(r"\U+4E2DB") == "中B"


def test_unknown_legacy_codepage_is_not_silently_replaced(tmp_path):
    source = dxf(
        tmp_path,
        "0\nSECTION\n2\nHEADER\n9\n$DWGCODEPAGE\n3\nANSI_936\n0\nENDSEC\n0\nEOF\n",
    )
    with pytest.raises(SnapshotError, match="code page"):
        snapshot_dxf(source)
