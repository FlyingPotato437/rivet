import { ArrowRight, DownloadSimple, FilePdf } from "@phosphor-icons/react";
const files = import.meta.glob("../../../examples/order-replay/*.pdf", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;
const steps = [
  [
    "Import the submittal and comments",
    "Create an order for any custom equipment category. Add drawing files, review markups, and saved EML emails. Choose the document role and revision. Supported email attachments are imported with their parent message.",
  ],
  [
    "Check the comment log",
    "Each row retains its original text, author and date when stated, source page, response, and review status. Open a row to correct fields. Link the drawing area it actually refers to, even when the markup is on a different page. Missing or uncertain details remain flagged.",
  ],
  [
    "Record responses and changes",
    "Write the manufacturer’s response and name its author. Set Open, Responded, or Closed explicitly. Compare text from two revisions and record confirmed changes, with related comments and the person who requested or approved them. Rivet does not suggest engineering fixes.",
  ],
  [
    "Approve a version of the record",
    "After reviewing every row, save an approved record. It can still contain open comments. A read-only link shows that exact version; later working edits do not alter it. Links expire after 30 days and can be revoked.",
  ],
  [
    "Prepare communication",
    "Add customer, manufacturer, and production recipients. Record changes create email drafts for those recipients. Download and send from your email app, with the Excel comment log or PDF response matrix. Nothing is sent automatically.",
  ],
];
export function OrderGuide({
  onNew,
}: {
  onNew: () => void;
  navigate: (to: string) => void;
}) {
  return (
    <main className="page guide-page order-replay-guide">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Comment and change records</span>
          <h1>Workflow guide</h1>
          <p>
            Build, review, and share the communication record for a custom
            order.
          </p>
        </div>
        <button className="primary" onClick={onNew}>
          New order <ArrowRight size={16} />
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
      <section className="order-guide-note">
        <h2>Current integration status</h2>
        <p>
          Document uploads, EML imports, source links, editable logs, text
          comparisons, approved snapshots, local read-only links, and exports
          are available. Sign-in and team membership use Clerk. Email setup and
          receiving access are shown in Workspace settings. Automatic forwarding
          requires a deployed receiving endpoint; visual drawing interpretation
          is not connected. Engineering checks and proposal tools are retained
          for later stages, outside the main record workflow.
        </p>
      </section>
    </main>
  );
}
