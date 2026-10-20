# Independent customer-input holdout

This directory contains a source-only, independently reviewed input-extraction case. It is held closed to extraction/configuration implementers until the implementation freeze. The original PDF remains ignored under `.data/real-document-corpus`; its URL, original-file hash, selected physical pages and customer-side source role are recorded in the manifest.

The final pre-prediction key contains 39 bounded scalar requirements and a complete inventory of 15 switchboard breaker symbols with 33 explicitly printed device values. It excludes inferred frame sizes, inferred electrical approvals and inferred voltage-to-ground values. Earlier pre-prediction schema/semantic QA revisions are retained in `label-history/`. No model predictions were used to make the labels.

These are assistant source-reviewed labels, not an engineer-certified answer key. Broader specification completeness, downstream equipment, wiring, and production-layout correctness are outside this bounded score.

No manufacturer-approved output exists in the acquired package. Consequently this can measure customer requirement extraction and bounded device recall; it cannot measure reconstruction of an approved manufacturer design, drafter time saved, or production engineering accuracy. Approved reconstruction eligibility remains false.

After the implementation has been frozen, the independent evaluator may run the original source files through the pipeline. The source-only prediction must be persisted before the key is opened, and implementation/source hashes and any failures or deferred coverage must remain in the report. A key-driven subject match, rating repair, or manufacturer-design-as-input shortcut is not allowed.
