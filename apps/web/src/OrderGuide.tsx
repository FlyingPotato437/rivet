import { ArrowRight, DownloadSimple, FilePdf } from "@phosphor-icons/react";
import { AdoptionReadiness } from "./Integrations";
const files = import.meta.glob("../../../examples/order-replay/*.pdf", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;
const steps = [
  [
    "Drop in the returned drawing",
    "Start with a marked-up PDF or saved EML email. Include the job number and revision when you know them. Rivet keeps the original, extracts available comments, and asks you to confirm uncertain matches.",
  ],
  [
    "Review the comments that need attention",
    "The comment log keeps the original wording and a link to its source page. Review repeated or previously open comments, changes after an approved record, and possible scope changes. These are review cues. Rivet does not decide engineering compliance or commercial entitlement.",
  ],
  [
    "Confirm the response and next step",
    "Use the original drawing beside each comment. Correct uncertain authors or page references, record the response and its author, and assign drafting work. AI-prepared work stays marked for review until a person accepts it.",
  ],
  [
    "Send the files your team already uses",
    "Download the Excel comment log, response PDF, or an email review brief. Choose recipients and send from your own email app. An approved record preserves who reviewed it and when; approval of the record is separate from engineering approval of the equipment.",
  ],
  [
    "Bring the next revision back",
    "Add the returned revision to the same order. Rivet compares the extracted comments with the earlier revision and surfaces items needing another look. A comment disappearing from the PDF does not prove the drawing was corrected; confirm that from the source.",
  ],
];
export function OrderGuide({
  navigate,
}: {
  onNew: () => void;
  navigate: (to: string) => void;
}) {
  return (
    <main className="page guide-page order-replay-guide">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Get started</span>
          <h1>From returned drawing to response</h1>
          <p>
            Start with one revision. Review the exceptions, then export the
            response.
          </p>
        </div>
        <button className="primary" onClick={() => navigate("decisions")}>
          Add returned drawing <ArrowRight size={16} />
        </button>
      </div>
      <div className="guide-steps">
        {steps.map(([title, text], i) => (
          <article key={title}>
            <span className="step-number">0{i + 1}</span>
            <div>
              <h3>{title}</h3>
              <p>{text}</p>
            </div>
          </article>
        ))}
      </div>
      <article className="guide-downloads">
        <div>
          <h2>Sample review documents</h2>
          <p>
            Fictional switchgear drawings and markups for exploring source links
            and revision comparisons. Your own orders do not depend on this
            package.
          </p>
          <button className="text-button" onClick={() => navigate("orders")}>
            Open your order list <ArrowRight size={15} />
          </button>
        </div>
        <div className="order-guide-files">
          {Object.entries(files)
            .filter(([p]) => /drawing|markups/.test(p))
            .map(([p, url]) => (
              <a href={url} key={p} download>
                <span>
                  <FilePdf size={17} />
                  {p.split("/").at(-1)}
                </span>
                <DownloadSimple size={16} />
              </a>
            ))}
        </div>
      </article>
      <AdoptionReadiness compact />
    </main>
  );
}
