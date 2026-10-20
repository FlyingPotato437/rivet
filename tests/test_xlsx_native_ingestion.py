"""Actual generated native workbooks must ingest with sparse read-only cells."""

from types import SimpleNamespace

from openpyxl import load_workbook
from openpyxl.cell.read_only import EmptyCell
from openpyxl.utils.cell import get_column_letter, range_boundaries

from backend.equipment.native_demo import baseline_graph, write_bom, write_native
from backend.equipment.native_model import inspect_native
from backend.equipment.native_schedules import add_native_schedules
from backend.ingestion import parser
from backend.storage.models import Span


class SpanCapture:
    def __init__(self):
        self.spans = []
        self.flushed = False

    def add(self, item):
        assert isinstance(item, Span)
        self.spans.append(item)

    def flush(self):
        self.flushed = True


def test_generated_native_bom_and_sparse_multiwidth_sheets_have_valid_cell_spans(
    tmp_path, monkeypatch
):
    drawing, bom = tmp_path / "actual-native.dxf", tmp_path / "actual-native.xlsx"
    write_native(drawing, baseline_graph(), "D-R1")
    write_bom(bom, baseline_graph(), "D")
    workbook = load_workbook(bom)
    add_native_schedules(workbook, inspect_native(drawing), "D-R1")
    # Genuine sparse XML rows in two widths. Read-only mode represents the
    # missing final cells with EmptyCell, which has no column_letter property.
    wide = workbook.create_sheet("Wide manufacturer notes")
    wide.append(["Equipment note"] + [f"Field {i}" for i in range(2, 31)])
    wide.append(["Retain previously approved trip wiring"])
    wide.cell(3, 30, "Keep the thirty-column layout")
    narrow = workbook.create_sheet("Two-column issue notes")
    narrow.append(["Topic", "Detail"])
    narrow.append(["Engineering review remains required"])
    workbook.save(bom)
    workbook.close()

    actual = load_workbook(bom, read_only=True, data_only=False)
    expected = {}
    has_sparse_tail = set()
    for sheet in actual:
        rows = list(sheet.iter_rows())
        for n, row in enumerate(rows[1:], 2):
            if not any(cell.value is not None for cell in row):
                continue
            expected[(sheet.title, n)] = (
                sheet.max_column,
                f"A{n}:{get_column_letter(len(row))}{n}",
            )
            if isinstance(row[-1], EmptyCell):
                has_sparse_tail.add(sheet.title)
    actual.close()
    assert {
        "Rivet wiring",
        "Rivet layout",
        "Wide manufacturer notes",
        "Two-column issue notes",
    } <= has_sparse_tail

    monkeypatch.setattr(parser, "BLOBS", tmp_path)
    document = SimpleNamespace(
        id="native-bom-document", blob=bom.name, meta={}, state="queued", coverage=[]
    )
    captured = SpanCapture()
    parser.parse(captured, document)
    assert captured.flushed and document.state == "needs_mapping"
    assert {c["label"] for c in document.coverage} == {
        "Equipment BOM",
        "Issue record",
        "Rivet wiring",
        "Rivet layout",
        "Wide manufacturer notes",
        "Two-column issue notes",
    }
    assert all(c["state"] == "processed" for c in document.coverage)
    observed = {}
    for span in captured.spans:
        key = (span.location["sheet"], span.location["row"])
        observed[key] = span.location["cells"]
        maximum, expected_range = expected[key]
        assert span.location["cells"] == expected_range
        left, first_row, right, last_row = range_boundaries(span.location["cells"])
        assert left == 1 and 1 <= right <= maximum
        assert first_row == last_row == span.location["row"]
        assert span.document_id == document.id and span.id
    assert observed == {key: value[1] for key, value in expected.items()}
    assert observed[("Wide manufacturer notes", 2)] == "A2:AD2"
    assert observed[("Two-column issue notes", 2)] == "A2:B2"
    assert {row["source_id"] for row in document.meta["rows"]} == {
        s.id for s in captured.spans
    }
