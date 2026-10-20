# Real equipment validation split

The registry freezes input pages before readers run. Keys were independently transcribed from rendered originals by an assistant; they are preliminary and need an electrical engineer's second review.

- **Development:** Kansas H3 and the already known May 15 → May 30 Control Center one-line change. Existing comment development pages remain development.
- **Unseen scanned schedules:** two new Kansas schedules, physical pages 240 and 253. Both are from the same manufacturer and binder as development. This tests unseen layouts/device sets, **not a new manufacturer**.
- **Unseen separate-project schedule:** Petersburg Scow Bay, physical page 35. Its text is CAD outlines, not a scan. The twelve-row BOM is a separate-project test.

`packages.json` is accepted by the existing real-package harness. Every local page copy retains unchanged rendering, parent URL, parent SHA-256 and physical-page map. Original files and copies remain under ignored `.data`.

Do not tune on held-out predictions and continue calling them unseen. A failed held-out page may become development only with a new frozen test set. No currently labelled real drawing revision pair is unseen.

References distinguish physical source rows from devices. The Kansas main schedule contains real repeated accessory continuation lines and a crossed-out row; these must not become duplicate installed breakers. Scow prints conflicting lockout labels between its BOM and elevation; the label preserves what is printed rather than correcting it silently.
