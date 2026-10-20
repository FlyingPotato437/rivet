"""Read only actual XLSX merge geometry; never forward-fill merged devices."""

import io
import posixpath
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from openpyxl.utils.cell import range_boundaries

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def merged_ranges(data):
    def xml(archive, name):
        raw = archive.read(name)
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("unsafe_workbook_xml")
        return ET.fromstring(raw)

    with ZipFile(io.BytesIO(data)) as archive:
        rels = {
            r.attrib["Id"]: r.attrib for r in xml(archive, "xl/_rels/workbook.xml.rels")
        }
        result = {}
        for sheet in xml(archive, "xl/workbook.xml").findall("m:sheets/m:sheet", NS):
            relationship = rels[sheet.attrib["{" + REL + "}id"]]
            if relationship.get("TargetMode") == "External":
                raise ValueError("external_worksheet_relationship")
            target = relationship["Target"]
            target = posixpath.normpath(
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.join("xl", target)
            )
            if not target.startswith("xl/worksheets/"):
                raise ValueError("unexpected_worksheet_relationship")
            ranges = [
                range_boundaries(item.attrib["ref"])
                for item in xml(archive, target).findall("m:mergeCells/m:mergeCell", NS)
            ]
            if len(ranges) > 10000:
                raise ValueError("workbook_merge_limit")
            result[sheet.attrib["name"]] = ranges
        return result
