"""A valid streaming workbook may omit its optional sheet dimension hint."""

import io
import re
from hashlib import sha256
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from backend.equipment.readers import read_source


def test_equipment_bom_without_dimension_keeps_actual_values(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["equipment_tag", "frame_a", "trip_a", "nameplate"])
    sheet.append(["CB-01", 150, 125, "INPUT MAIN"])
    buffer = io.BytesIO()
    book.save(buffer)
    rewritten = io.BytesIO()
    with ZipFile(buffer) as original, ZipFile(rewritten, "w", ZIP_DEFLATED) as target:
        for entry in original.infolist():
            data = original.read(entry.filename)
            if entry.filename.startswith("xl/worksheets/"):
                data = re.sub(rb"<dimension\b[^>]*/>", b"", data)
            target.writestr(entry, data)
    path = tmp_path / "streaming-bom.xlsx"
    path.write_bytes(rewritten.getvalue())
    report, facts = read_source(
        {
            "id": "streaming-bom",
            "name": path.name,
            "path": str(path),
            "role": "bom",
            "revision": "A",
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    )
    assert report["status"] == "parsed"
    values = {(f["device_tag"], f["field"]): f["value"] for f in facts}
    assert values[("CB-01", "frame_a")] == 150
    assert values[("CB-01", "trip_a")] == 125
    assert values[("CB-01", "nameplate")] == "INPUT MAIN"
