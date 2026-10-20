"""Acquire unchanged public-owner electrical bid/addendum originals, not fixtures.

This is provenance collection only. It does not create answer keys, score Rivet,
claim completed customer review cycles, or count each addendum as a new project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / ".data/real-document-corpus/new-official"


def source(name, url, revision, *, role="specification", side="requirements"):
    return {"name": name, "url": url, "revision": revision, "role": role, "side": side}


PACKAGES = [
    {
        "id": "durham-26-1047",
        "title": "GoDurham 4,000 A electrical switchboards — procurement addendum",
        "publisher": "City of Durham, North Carolina",
        "package_type": "bid_addendum",
        "sources": [
            source(
                "Switchboard-Addendum-1.pdf",
                "https://www.durhamnc.gov/DocumentCenter/View/68515/26-1047-Switchboard-Addendum-1?bidId=",
                "Addendum 1, 2026-04-10",
            )
        ],
    },
    {
        "id": "hopewell-hwr-switchgear",
        "title": "Hopewell Water Renewal medium-voltage switchgear replacement",
        "publisher": "City of Hopewell, Virginia",
        "package_type": "bid_specifications_and_reissue",
        "sources": [
            source(
                "Medium-Voltage-Switchgear-2022.pdf",
                "https://hopewellva.gov/DocumentCenter/View/719/Medium-Voltage-Switchgear-PDF?bidId=",
                "Issued May 2022",
                side="baseline",
            ),
            source(
                "Medium-Voltage-Switchgear-Reissue.pdf",
                "https://hopewellva.gov/DocumentCenter/View/1305/Reissue-05-24-HWR---Medium-Voltage-Switchgear-Replacement---Bid-Specs?bidId=",
                "Reissue IFB 05-24",
                side="target",
            ),
        ],
    },
    {
        "id": "maine-cmcc-generator",
        "title": "Central Maine Community College generator — switchboard addendum",
        "publisher": "State of Maine Bureau of General Services",
        "package_type": "bid_addendum",
        "sources": [
            source(
                "CMCC-Generator-Addendum-1.pdf",
                "https://www.maine.gov/dafs/bgs/sites/maine.gov.dafs.bgs/files/inline-files/2972%2020190523CMCCGenAdd1.pdf",
                "Addendum 1, May 2019",
            )
        ],
    },
    {
        "id": "maine-nmcc-transformer",
        "title": "Northern Maine Community College Edmunds Center service transformer replacement",
        "publisher": "State of Maine Bureau of General Services",
        "package_type": "bid_addendum",
        "sources": [
            source(
                "NMCC-Transformer-Addendum-1.pdf",
                "https://www.maine.gov/dafs/bgs/sites/maine.gov.dafs.bgs/files/inline-files/3618%20Addendum1%20NMCC.pdf",
                "Addendum 1, 2023-12-12",
            )
        ],
    },
    {
        "id": "idea-school-switchgear-2024",
        "title": "IDEA Public Schools electrical switchgear upgrades at multiple campuses",
        "publisher": "IDEA Public Schools",
        "package_type": "bid_specifications",
        "sources": [
            source(
                "IDEA-Switchgear-Specifications.pdf",
                "https://ideapublicschools.org/wp-content/uploads/2024/03/2024-03-15-Electrical-Switchgear-Upg-CSP22-ESGU-0424-SS-Specs-Binder.pdf",
                "Bid specifications, 2024-03-15",
            )
        ],
    },
    {
        "id": "jea-arlington-east-wrf",
        "title": "Arlington East water reclamation facility switchgear replacement",
        "publisher": "JEA municipal utility",
        "package_type": "bid_specifications",
        "sources": [
            source(
                "JEA-Arlington-East-Switchgear.pdf",
                "https://www.jea.com/Pdf/Download/12884922795",
                "Technical specifications, 2017",
            )
        ],
    },
    {
        "id": "fremont-metal-enclosed-switchgear",
        "title": "Metal-enclosed switchgear purchase contract",
        "publisher": "City of Fremont, Nebraska",
        "package_type": "bid_specifications",
        "sources": [
            source(
                "Fremont-Switchgear-Bid.pdf",
                "https://www.fremontne.gov/DocumentCenter/View/4744/Contract-for-Purchase-of-Metal-Enclosed-Switchgear---January-2018---Bid-Documents?bidId=",
                "Bid documents, January 2018",
            )
        ],
    },
    {
        "id": "san-marcos-rattler-transformer",
        "title": "Rattler Substation transformer T1 purchase",
        "publisher": "City of San Marcos, Texas",
        "package_type": "bid_specifications",
        "sources": [
            source(
                "Rattler-T1-Transformer-IFB.pdf",
                "https://www.sanmarcostx.gov/DocumentCenter/View/15932/220-057-Rattler-Substation-Transformer-IFB?bidId=",
                "IFB 220-057",
            )
        ],
    },
    {
        "id": "hanford-a6-feeders",
        "title": "A6 feeders to new HLW switchgear building study",
        "publisher": "Hanford Mission Integration Solutions, US DOE site contractor",
        "package_type": "engineering_study_rfp",
        "sources": [
            source(
                "Hanford-A6-Switchgear-SOW.pdf",
                "https://hmis.hanford.gov/files.cfm/Attach_1_SOW_A6_Feeders_to_New_HLW_Switchgear_Building_Study.pdf",
                "RFP 00372979, Rev 00, 2024-04-08",
            )
        ],
    },
    {
        "id": "nyogs-altona-44750",
        "title": "Altona Correctional Facility switchgear replacement addendum",
        "publisher": "New York State Office of General Services",
        "package_type": "bid_addendum",
        "sources": [
            source(
                "Altona-Switchgear-Addendum-1.pdf",
                "https://online.ogs.ny.gov/dncaddenda/44750/Addendum%2001%20E.pdf",
                "Project 44750-E, Addendum 1, 2017-10-06",
            )
        ],
    },
    {
        "id": "unc-charlotte-grid-automation",
        "title": "Electrical Grid Automation Phase I — main substation switchgear",
        "publisher": "University of North Carolina at Charlotte",
        "source_page": "https://facilities.charlotte.edu/2026/04/14/electrical-grid-automation-phase-i/",
        "package_type": "bid_addendum_series",
        "sources": [
            source(
                "Notice-to-Bidders.pdf",
                "https://facilities.charlotte.edu/wp-content/uploads/sites/1297/2026/04/Notice-to-Bidders-Elec-Grid-Automation-Phase-1.pdf",
                "Notice, April 2026",
                side="baseline",
            ),
            source(
                "Addendum-1.pdf",
                "https://facilities.charlotte.edu/wp-content/uploads/sites/1297/2026/04/2026.05.06-Addendum-1-1.pdf",
                "Addendum 1",
                side="target",
            ),
            source(
                "Addendum-2.pdf",
                "https://facilities.charlotte.edu/wp-content/uploads/sites/1297/2026/05/2026.05.06-Addendum-2-Final.pdf",
                "Addendum 2",
                side="target",
            ),
            source(
                "Addendum-3.pdf",
                "https://facilities.charlotte.edu/wp-content/uploads/sites/1297/2026/05/2026.05.07-Addendum-3.pdf",
                "Addendum 3",
                side="target",
            ),
            source(
                "Addendum-4.pdf",
                "https://facilities.charlotte.edu/wp-content/uploads/sites/1297/2026/05/2026.05.20-Addendum-4.pdf",
                "Addendum 4",
                side="target",
            ),
            source(
                "Addendum-5.pdf",
                "https://facilities.charlotte.edu/wp-content/uploads/sites/1297/2026/05/2026.05.28-Addendum-5.pdf",
                "Addendum 5",
                side="target",
            ),
        ],
    },
    {
        "id": "tctc-anderson-switchboard",
        "title": "Anderson Campus switchboard procurement and rebid",
        "publisher": "Tri-County Technical College",
        "source_page": "https://www.tctc.edu/about-us/accountability/purchasing/solicitation-postings/",
        "package_type": "bid_and_rebid_with_drawing",
        "sources": [
            source(
                "Original-Invitation-to-Bid.pdf",
                "https://www.tctc.edu/media/ruopkbza/tctc-24-switchboard.pdf",
                "TCTC-24 original bid",
                side="baseline",
            ),
            source(
                "Rebid-Invitation-to-Bid.pdf",
                "https://www.tctc.edu/media/x2ildbwc/tctc-24-switchboard-2-revised.pdf",
                "TCTC-24 rebid 2",
                side="target",
            ),
            source(
                "Anderson-Switchboard-Drawing.pdf",
                "https://www.tctc.edu/media/pfhdrjnr/anderson-switchboard-drawing.pdf",
                "Attachment 1 to rebid",
                role="drawing",
                side="target",
            ),
        ],
    },
    {
        "id": "austin-power-transformers-eal3030",
        "title": "Austin Energy power distribution transformer procurement",
        "publisher": "City of Austin, Texas",
        "source_page": "https://financeonline.austintexas.gov/afo/account_services/solicitation/solicitation_details.cfm?sid=143886",
        "package_type": "bid_specification_and_addenda",
        "sources": [
            source(
                "Transformer-Specification.pdf",
                "https://assets.austintexas.gov/financeonline/downloads/vc_files/RFP_1100_EAL3030/RFP_1100_EAL3030_ATT_1_v1.pdf",
                "Attachment 1, 2026-03-30",
                side="baseline",
            ),
            source(
                "Addendum-1.pdf",
                "https://assets.austintexas.gov/financeonline/downloads/vc_files/RFP_1100_EAL3030/RFP_1100_EAL3030_ADD_1_v1.pdf",
                "Addendum 1, 2026-04-02",
                side="target",
            ),
            source(
                "Addendum-2.pdf",
                "https://assets.austintexas.gov/financeonline/downloads/vc_files/RFP_1100_EAL3030/RFP_1100_EAL3030_ADD_2_v1.pdf",
                "Addendum 2, 2026-04-16",
                side="target",
            ),
        ],
    },
    {
        "id": "austin-multitap-transformer-mlj1026",
        "title": "Austin Water 2500/2800 kVA multi-tap transformer procurement",
        "publisher": "City of Austin, Texas",
        "source_page": "https://financeonline.austintexas.gov/afo/account_services/solicitation/solicitation_details.cfm?sid=141770",
        "package_type": "bid_with_transformer_drawing",
        "sources": [
            source(
                "Multi-Tap-Transformer-Scope.pdf",
                "https://assets.austintexas.gov/financeonline/downloads/vc_files/IFB_2200_MLJ1026/IFB_2200_MLJ1026_0500_v2.pdf",
                "Scope v2, 2024-11-07",
            ),
            source(
                "Multi-Tap-Transformer-Bid.pdf",
                "https://assets.austintexas.gov/financeonline/downloads/vc_files/IFB_2200_MLJ1026/IFB_2200_MLJ1026_BID_DOC_v1.pdf",
                "Bid v1, 2024-11-04",
            ),
        ],
    },
    {
        "id": "petersburg-scow-bay-gen2",
        "title": "Scow Bay Generator No. 2 switchgear procurement",
        "publisher": "Petersburg Borough, Alaska",
        "source_page": "https://www.petersburgak.gov/power-light/page/invitation-bid-scow-bay-2-switchgear-bid",
        "package_type": "bid_specifications_and_addendum",
        "sources": [
            source(
                "Scow-Bay-Switchgear-Bid.pdf",
                "https://www.petersburgak.gov/sites/default/files/fileattachments/power_amp_light/page/18954/petersburg_borough_scow_bay_generator_2_switchgear_bid_package_8.22.25.pdf",
                "Bid package, 2025-08-22",
                side="baseline",
            ),
            source(
                "Scow-Bay-Switchgear-Addendum-1.pdf",
                "https://www.petersburgak.gov/sites/default/files/fileattachments/power_amp_light/page/18954/addendum_1_scow_bay_gen_2_switchgear.pdf",
                "Addendum 1",
                side="target",
            ),
        ],
    },
    {
        "id": "danville-pole-transformers-2526075",
        "title": "Single-phase pole-mount transformers — bid and questions/answers",
        "publisher": "City of Danville, Virginia",
        "source_page": "https://www.danville-va.gov/bids.aspx?bidid=4098",
        "package_type": "bid_specifications_and_addendum",
        "sources": [
            source(
                "Pole-Mount-Transformer-Bid.pdf",
                "https://www.danville-va.gov/DocumentCenter/View/39156",
                "QB 25-26-075, May 2026",
                side="baseline",
            ),
            source(
                "Pole-Mount-Transformer-QA.pdf",
                "https://www.danville-va.gov/DocumentCenter/View/39213",
                "Addendum 1, questions and answers",
                side="target",
            ),
        ],
    },
    {
        "id": "kansas-docking-switchboard",
        "title": "Docking State Office Building — Square D approval and returned review drawings",
        "publisher": "Kansas Legislature, published committee testimony archive",
        "manufacturer": "Schneider Electric / Square D",
        "package_type": "actual_manufacturer_approval_and_returned_review_binder",
        "returned_customer_review_established": True,
        "paired_corrected_post_review_release_established": False,
        "sources": [
            source(
                "Docking-SquareD-Approval-Drawings.pdf",
                "https://kslegislature.gov/li_2016/b2015_16/committees/ctte_jt_bldg_constr_1/documents/testimony/20160202_51.pdf",
                "Published binder; dated 2012 issues and July 2012 review",
                role="drawing",
                side="target",
            ),
        ],
        "observed_scope": "One project: approval material, drawings and actual returned Locher review stamps. Physical pages 241–246 contain the complete H3 subpackage; pages 194 and252 contain dated issues of the same Control Center No.1 one-line. No completed corrected manufacture release is inferred.",
    },
    {
        "id": "nydec-dollinger-ge-record",
        "title": "Dollinger post-remedial operation and maintenance plan with GE supplier record appendix",
        "publisher": "New York State Department of Environmental Conservation",
        "package_type": "operations_record_with_supplier_submittal_appendix",
        "sources": [
            source(
                "Dollinger-Post-Remedial-OM.pdf",
                "https://extapps.dec.ny.gov/data/DecDocs/828078/Work%20Plan.HW.828078.1994-11-30.POST-REMEDIAL%20O%20AND%20M%20.pdf",
                "November 1994 record",
                role="drawing",
                side="target",
            ),
        ],
        "observed_scope": "Mixed operation/environmental record; GE electrical supplier transmittal is physical page188. Not a complete modern switchgear customer-review package.",
    },
    {
        "id": "va-birmingham-switchgear-phase3",
        "title": "Birmingham VA Medical Center switchgear replacement phase 3",
        "publisher": "US Department of Veterans Affairs",
        "package_type": "bid_specifications",
        "sources": [
            source(
                "VA-Switchgear-Phase3-Specifications.pdf",
                "https://www.vendorportal.ecms.va.gov/FBODocumentServer/DocumentServer.aspx?DocumentId=4571696&FileName=36C24718B0635-0001007.pdf",
                "Project 521-16-103, solicitation attachment 0001007",
            )
        ],
    },
]


def _collect(work):
    package, index, item, corpus = work
    folder = corpus / package["id"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / item["name"]
    url = item["url"]
    status = "previously_acquired"
    if not path.is_file():
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            content = bytearray()
            for block in response.iter_bytes():
                content.extend(block)
                if len(content) > 100_000_000:
                    raise ValueError("Original exceeds the 100 MB acquisition limit.")
            if not content.startswith(b"%PDF"):
                raise ValueError("Publisher did not return an original PDF.")
            final_url = str(response.url)
        data = bytes(content)
        status = "downloaded"
    else:
        final_url = url
        data = path.read_bytes()
    actual_hash = hashlib.sha256(data).hexdigest()
    if item.get("pinned_sha256") and actual_hash != item["pinned_sha256"]:
        raise ValueError(
            "The original changed from its recorded hash; review and repin explicitly."
        )
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        if pdf.needs_pass or not len(pdf):
            raise ValueError("The original is encrypted or has no readable pages.")
        pages = len(pdf)
        first_page = " ".join(pdf[0].get_text().split())[:1000]
        annotations = sum(len(list(page.annots() or [])) for page in pdf)
    if status == "downloaded":
        path.write_bytes(data)
    return {
        **item,
        "id": package["id"] + ":" + str(index + 1),
        "path": path.relative_to(corpus).as_posix(),
        "sha256": actual_hash,
        "bytes": len(data),
        "pages": pages,
        "native_annotation_objects": annotations,
        "final_url": final_url,
        "acquisition": status,
        "first_page_identity_excerpt": first_page,
        "original_unchanged": True,
    }


def acquire(corpus=CORPUS, *, workers=5):
    corpus = Path(corpus)
    corpus.mkdir(parents=True, exist_ok=True)
    previous_file = corpus / "manifest.json"
    previous = json.loads(previous_file.read_text()) if previous_file.is_file() else {}
    pinned = {
        (package["id"], item["name"]): item["sha256"]
        for package in previous.get("packages", [])
        for item in package["sources"]
    }
    work = [
        (p, n, {**src, "pinned_sha256": pinned.get((p["id"], src["name"]))}, corpus)
        for p in PACKAGES
        for n, src in enumerate(p["sources"])
    ]
    results, failures = {}, []

    def attempt(job):
        try:
            return job, _collect(job), None
        except (httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
            return job, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for job, result, error in pool.map(attempt, work):
            package, _, item, _ = job
            if error:
                failures.append(
                    {"package_id": package["id"], "url": item["url"], "error": error}
                )
                print(json.dumps({"failed": package["id"], "error": error}), flush=True)
            else:
                results.setdefault(package["id"], []).append(result)
                print(
                    json.dumps(
                        {
                            "package": package["id"],
                            "file": result["name"],
                            "pages": result["pages"],
                        }
                    ),
                    flush=True,
                )
    packages = []
    for package in PACKAGES:
        sources = results.get(package["id"], [])
        if not sources:
            continue
        packages.append(
            {
                **{k: v for k, v in package.items() if k != "sources"},
                "kind": "real_public",
                "sources": sources,
                "original_source_count": len(sources),
                "project_package_count": 1,
                "independently_labeled": False,
                "completed_customer_return_cycle_established": False,
                "provenance": "Unchanged original published by the project owner or authorized government-site contractor. Public bid changes are not relabeled as customer-returned manufacturing revisions.",
            }
        )
    manifest = {
        "schema_version": "rivet.real-packages.v1",
        "collected_at": datetime.now(UTC).isoformat(),
        "packages": packages,
        "acquisition_failures": failures,
        "rights": "Original publishers retain their rights. Public access is not treated as a redistribution licence; originals are local research data outside Git.",
        "limits": [
            "One real project is one package even when it has multiple addenda or bid revisions.",
            "No synthetic circuit or manufacturer datasheet page is counted as a real project package.",
            "Acquisition and valid PDF parsing do not establish extraction accuracy, engineering correctness, or readiness.",
            "Independent human answer keys and actual completed review-cycle evidence are separate requirements.",
        ],
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "manifest": str(corpus / "manifest.json"),
                "packages": len(packages),
                "originals": sum(len(p["sources"]) for p in packages),
                "physical_pages": sum(
                    s["pages"] for p in packages for s in p["sources"]
                ),
                "failures": len(failures),
            }
        ),
        flush=True,
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CORPUS)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    acquire(args.root, workers=args.workers)
