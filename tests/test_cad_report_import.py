"""Synthetic format controls; none count as real manufacturer accuracy."""

import hashlib
import io

import pytest
from openpyxl import Workbook

from backend.equipment.cad_reports import read_cad_report
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    """Pure adapters do not read or rebuild any database."""
    yield


def source(tmp_path, data, name="report.csv"):
    p = tmp_path / name
    p.write_bytes(data)
    return SourceInput(
        id="source",
        name=name,
        path=str(p),
        role="bom",
        revision="B",
        sha256=hashlib.sha256(data).hexdigest(),
    )


def test_autodesk_component_columns_preserve_raw_rating(tmp_path):
    s = source(
        tmp_path,
        b"Title,,,\nTAGNAME,CAT,MFG,CNT,RATING1\n52-12,ABC,Maker,2,100/12.5kV\n",
    )
    report, facts = read_cad_report(s)
    assert report["imported_counts"]["components"] == 1
    assert {f["field"] for f in facts} == {"part_number", "quantity"}
    assert (
        report["components"][0]["properties"]["raw_values"]["RATING1"] == "100/12.5kV"
    )
    assert not any(f["field"] == "frame_a" for f in facts)


def test_autodesk_wire_from_to_does_not_invent_endpoint(tmp_path):
    s = source(
        tmp_path, b"WIRENO,CMP1,PIN1,CMP2,PIN2\nW1,52-12,1,86T,A1\nW2,,2,86T,A2\n"
    )
    report, facts = read_cad_report(s)
    assert not facts
    assert report["imported_counts"]["connections"] == 2
    assert report["imported_counts"]["connections_with_unknown_endpoints"] == 1
    assert report["connections"][1]["from"]["device_tag"] is None
    assert all(not x["geometry_verified"] for x in report["connections"])


def test_eplan_full_device_structure_is_preserved(tmp_path):
    s = source(
        tmp_path,
        b"Connection designation;Source;Target\nW3;=A1+P1-52:12;=A1+P1-K1:A1\n",
    )
    report, _ = read_cad_report(s)
    assert report["connections"][0]["from"] == {"device_tag": "=A1+P1-52", "pin": "12"}


def test_tallied_bom_not_split_into_invented_devices(tmp_path):
    s = source(tmp_path, b"MFG,CAT,QTY\nMaker,ABC,4\n")
    report, facts = read_cad_report(s)
    assert report["imported_counts"]["bom_rows"] == 1
    assert not report["components"] and not facts


def test_unrelated_workbook_not_claimed_as_cad(tmp_path):
    w = Workbook()
    w.active.append(["Schedule of works", "Location", "Price"])
    w.active.append(["Painting", "Room 2", 100])
    out = io.BytesIO()
    w.save(out)
    assert read_cad_report(source(tmp_path, out.getvalue(), "works.xlsx")) is None


def test_merged_and_formula_rows_are_uncertain(tmp_path):
    w = Workbook()
    q = w.active
    q.append(["TAGNAME", "CAT", "CNT"])
    q.append(["52-12", "ABC", 1])
    q.append(["52-13", "ABC", 1])
    q.merge_cells("A2:A3")
    q.append(["52-14", "ABC", "=2+2"])
    out = io.BytesIO()
    w.save(out)
    report, facts = read_cad_report(source(tmp_path, out.getvalue(), "components.xlsx"))
    assert not facts
    assert len(report["uncertain"]) == 3


def test_xml_missing_and_repeated_pins_are_retained(tmp_path):
    s = source(
        tmp_path,
        b'<HarnessData><Harness><Connector RefDes="FDR 12"><Property Name="CAT" Value="P1"/><Pin Definition="H"/><Pin Definition="H"/></Connector><Wire Name="W1"><Pin1 ConnectorRefDes="" ConnectorPin="H"/><Pin2 ConnectorRefDes="FDR 12" ConnectorPin="H"/></Wire></Harness></HarnessData>',
        "actual.xml",
    )
    report, facts = read_cad_report(s)
    assert report["components"][0]["tag"] == "FDR 12"
    assert report["connections"][0]["status"] == "unknown_endpoint"
    assert len(report["warnings"]) == 2
    assert len(facts) == 1


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_entities_and_utf16_rejected(tmp_path, encoding):
    xml = '<!DOCTYPE HarnessData [<!ENTITY a "oops">]><HarnessData><Harness>&a;</Harness></HarnessData>'
    with pytest.raises(ValueError):
        read_cad_report(source(tmp_path, xml.encode(encoding), "bad.xml"))


def test_xml_depth_hash_and_unrelated_format(tmp_path):
    with pytest.raises(ValueError, match="depth"):
        read_cad_report(
            source(
                tmp_path,
                (
                    "<HarnessData>" + "<a>" * 30 + "</a>" * 30 + "</HarnessData>"
                ).encode(),
                "deep.xml",
            )
        )
    s = source(tmp_path, b"<Other/>\n", "other.xml")
    assert read_cad_report(s) is None
    p = source(tmp_path, b"TAGNAME,CAT\n52-12,P1\n")
    with open(p.path, "ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="hash"):
        read_cad_report(p)
