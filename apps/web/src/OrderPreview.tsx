import { useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  ChatText,
  FileText,
  WarningCircle,
  Check,
} from "@phosphor-icons/react";
import { Mark } from "./ui";
import "./order-preview.css";
const sample = [
  {
    number: "07",
    text: "Please confirm the cable entry location shown on drawing E-10.",
    author: "Priya Mehta",
    page: "2",
    target: "10",
    status: "Open",
    review: false,
    response:
      "The markup appears on page 2. The referenced drawing is on page 10; the PM needs to confirm the link.",
  },
  {
    number: "08",
    text: "Update the enclosure finish to match the approved finish schedule.",
    author: "Daniel Reyes",
    page: "6",
    target: "6",
    status: "Responded",
    review: true,
    response:
      "Finish updated to RAL 7035 in revision 03. Recorded by Lena Ortiz, project manager.",
  },
  {
    number: "09",
    text: "Show the revised mounting dimensions on the general arrangement.",
    author: "Priya Mehta",
    page: "4",
    target: "4",
    status: "Closed",
    review: true,
    response:
      "Dimensions revised in drawing E-04. Customer approval recorded against revision 03.",
  },
];
export function SampleOrderPreview() {
  const [selected, setSelected] = useState(0),
    [tab, setTab] = useState("Comment log"),
    [source, setSource] = useState(false);
  const c = sample[selected];
  return (
    <div
      className="op-preview"
      aria-label="Interactive fictional comment record"
    >
      <aside className="op-sidebar">
        <div className="op-brand">
          <Mark small />
          rivet<span>.</span>
        </div>
        <div className="op-space">
          <span>L</span>
          <div>
            Larkspur Electric<small>Custom equipment</small>
          </div>
        </div>
        <span className="op-nav-label">Workspace</span>
        <a className="active" href="#orders">
          <ChatText size={15} />
          Orders
        </a>
        <a href="#decisions">
          <WarningCircle size={15} />
          Needs review
        </a>
        <div className="op-sidebar-footer">Comment and change records</div>
      </aside>
      <div className="op-main">
        <div className="op-topbar">
          <span>
            Orders <ArrowRight size={10} />
            Custom switchgear
          </span>
          <span>Fictional example</span>
        </div>
        <header className="op-order-heading">
          <div>
            <span>ORD-2418 · Customer submittal</span>
            <h3>Westbridge expansion</h3>
          </div>
          <a href="#orders">
            Open workspace <ArrowUpRight size={13} />
          </a>
        </header>
        <div className="op-health">
          <span>3 comments</span>
          <span>1 needs review</span>
          <span>Revision 03</span>
        </div>
        <nav className="op-tabs" aria-label="Sample record views">
          {["Comment log", "Change record"].map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        {tab === "Comment log" ? (
          <div className="op-review">
            <div className="op-queue">
              {sample.map((row, i) => (
                <button
                  key={row.number}
                  className={i === selected ? "selected" : ""}
                  aria-pressed={i === selected}
                  onClick={() => {
                    setSelected(i);
                    setSource(false);
                  }}
                >
                  <span>
                    {row.review ? (
                      <Check size={14} />
                    ) : (
                      <WarningCircle size={14} />
                    )}
                    Comment {row.number}
                  </span>
                  <strong>{row.text}</strong>
                  <small>
                    {row.author} · {row.status}
                  </small>
                </button>
              ))}
            </div>
            <div className="op-detail" key={c.number}>
              <span className="op-detail-label">
                Comment {c.number}
                <span className={c.review ? "pass" : "fail"}>
                  {c.review ? "PM reviewed" : "Needs review"}
                </span>
              </span>
              <h4>{c.text}</h4>
              <p>{c.author} · October 1 · Rev 03</p>
              <div className="op-values">
                <div>
                  <span>Markup location</span>
                  <strong>p. {c.page}</strong>
                  <button onClick={() => setSource(!source)}>
                    <FileText size={12} />
                    Open source <ArrowUpRight size={11} />
                  </button>
                </div>
                <ArrowRight size={19} />
                <div>
                  <span>Referenced drawing</span>
                  <strong>p. {c.target}</strong>
                  <span>
                    {c.review ? "Location confirmed" : "Confirm location"}
                  </span>
                </div>
              </div>
              <div className="op-evidence-note">
                <p>
                  {source ? `Original annotation: “${c.text}”` : c.response}
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="op-history">
            <h4>Revision 02 → 03</h4>
            <div>
              <span>
                <FileText size={17} />
              </span>
              <article>
                <strong>Enclosure finish</strong>
                <p>RAL 9002 → RAL 7035</p>
                <span>Comment 08 · Requested by Daniel Reyes</span>
              </article>
            </div>
            <div>
              <span>
                <Check size={17} />
              </span>
              <article>
                <strong>Mounting dimensions</strong>
                <p>Updated drawing E-04, with customer approval recorded.</p>
                <span>Comment 09 · Reviewed by Lena Ortiz</span>
              </article>
            </div>
            <p>
              Every recorded change retains its author, date, reason, and
              source.
            </p>
          </div>
        )}
        <footer className="op-command">
          <Mark small />
          <span>Comments, responses, and changes in one record.</span>
          <a href="#orders" aria-label="Open order records">
            <ArrowRight size={15} />
          </a>
        </footer>
      </div>
    </div>
  );
}
