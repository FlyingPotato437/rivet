import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, UploadSimple } from "@phosphor-icons/react";
import { api, apiFetch, when } from "./api";
import { useAccount } from "./Auth";
import { ErrorNote } from "./ui";
import {
  manufacturerProfileStatus,
  manufacturerProposal,
  manufacturerRuleDescription,
  manufacturerSourceLocation,
  manufacturerSourcePath,
  type ManufacturerProfileRecord,
  type ManufacturerProfileSource,
} from "./manufacturer-profile-types";
import "./manufacturer-profile-settings.css";

export function ManufacturerProfileSettings() {
  const admin = useAccount().role === "org:admin";
  const query = useQuery({
    queryKey: ["manufacturer-profiles"],
    queryFn: () =>
      api<{ profiles: ManufacturerProfileRecord[] }>("/equipment/profiles"),
  });
  const [uploading, setUploading] = useState(false);
  const [proposal, setProposal] = useState("");
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function change(path: string, body: unknown, message: string) {
    setBusy(path);
    setError("");
    setNotice("");
    try {
      await api(path, body);
      await query.refetch();
      setNotice(message);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy("");
    }
  }

  async function openSource(
    record: ManufacturerProfileRecord,
    source: ManufacturerProfileSource,
  ) {
    const path = manufacturerSourcePath(
      record.source_urls?.[source.document_id],
    );
    if (!path) return;
    const preview = window.open("about:blank", "_blank");
    if (preview) {
      preview.opener = null;
      preview.document.title = "Opening profile source…";
      preview.document.body.textContent = "Opening your source document…";
    }
    setError("");
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const blob = await Promise.race([
        (async () => {
          const response = await apiFetch(path, { signal: controller.signal });
          if (!response.ok)
            throw new Error(
              "This source is no longer available. Refresh the profile before reviewing it.",
            );
          return response.blob();
        })(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(
              new Error(
                "The source took too long to open. Check your sign-in and try again.",
              ),
            );
          }, 30_000);
        }),
      ]);
      const url = URL.createObjectURL(blob);
      const page = source.location.page;
      const target =
        url + (typeof page === "number" && page > 0 ? `#page=${page}` : "");
      if (preview) preview.location.href = target;
      else {
        const link = document.createElement("a");
        link.href = url;
        link.download = source.name;
        link.click();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      preview?.close();
      setError((e as Error).message);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  return (
    <section
      className="manufacturer-profiles settings-section"
      aria-labelledby="manufacturer-profile-title"
    >
      <header className="manufacturer-profiles-header">
        <div>
          <h3 id="manufacturer-profile-title">Manufacturer profiles</h3>
          <p>
            Device names, part compatibility and layout rules, tied to your
            company’s source documents.
          </p>
        </div>
        {admin && (
          <button
            className="secondary"
            onClick={() => setUploading(!uploading)}
            disabled={!!busy}
          >
            <UploadSimple size={16} />{" "}
            {uploading ? "Close upload" : "Upload proposal"}
          </button>
        )}
      </header>
      {error && <ErrorNote message={error} />}
      {query.isError && <ErrorNote message={(query.error as Error).message} />}
      {notice && (
        <p className="manufacturer-profile-notice" role="status">
          {notice}
        </p>
      )}
      {uploading && admin && (
        <form
          className="manufacturer-proposal"
          onSubmit={async (e) => {
            e.preventDefault();
            let manifest;
            try {
              manifest = manufacturerProposal(proposal);
            } catch (e) {
              setError((e as Error).message);
              return;
            }
            if (
              await change(
                "/equipment/profiles",
                { manifest },
                "Proposal saved. Review its full rules and source evidence before enabling it.",
              )
            ) {
              setUploading(false);
              setProposal("");
              setFileName("");
            }
          }}
        >
          <label className="manufacturer-file-upload">
            <span>Profile JSON file</span>
            <input
              type="file"
              accept=".json,application/json"
              disabled={!!busy}
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                setError("");
                try {
                  if (file.size > 400_000)
                    throw new Error("Use a profile JSON file under 400 KB.");
                  const text = await file.text();
                  manufacturerProposal(text);
                  setProposal(text);
                  setFileName(file.name);
                } catch (e) {
                  setProposal("");
                  setFileName("");
                  setError((e as Error).message);
                }
              }}
            />
          </label>
          {fileName && <p>{fileName} · Ready to save as a proposal</p>}
          <details>
            <summary>Or paste the profile JSON</summary>
            <label>
              Profile contents
              <textarea
                rows={8}
                value={proposal}
                onChange={(e) => setProposal(e.target.value)}
                spellCheck={false}
                maxLength={400_000}
              />
            </label>
          </details>
          <p>
            Uploading never enables rules. A company administrator reviews the
            entire profile first.
          </p>
          <button
            className="primary"
            type="submit"
            disabled={!!busy || !proposal.trim()}
          >
            {busy === "/equipment/profiles"
              ? "Saving proposal…"
              : "Save proposal"}
          </button>
        </form>
      )}
      {query.isPending && <p role="status">Loading manufacturer profiles…</p>}
      {query.data && !query.data.profiles.length && (
        <p className="manufacturer-profile-empty">
          No profiles yet. A company administrator can upload a source-cited
          rule pack for review.
        </p>
      )}
      <div className="manufacturer-profile-list">
        {query.data?.profiles.map((record) => (
          <ProfileRow
            key={record.id}
            record={record}
            admin={admin}
            busy={!!busy}
            change={change}
            openSource={openSource}
          />
        ))}
      </div>
      <p className="manufacturer-profile-footnote">
        Company review enables these specific rules. It does not approve
        engineering, certify compliance or release production.
      </p>
    </section>
  );
}

function ProfileRow({
  record,
  admin,
  busy,
  change,
  openSource,
}: {
  record: ManufacturerProfileRecord;
  admin: boolean;
  busy: boolean;
  change: (path: string, body: unknown, message: string) => Promise<boolean>;
  openSource: (
    record: ManufacturerProfileRecord,
    source: ManufacturerProfileSource,
  ) => Promise<void>;
}) {
  const [checked, setChecked] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [reason, setReason] = useState("");
  const status = manufacturerProfileStatus(record);
  const manifest = record.manifest;
  return (
    <details className="manufacturer-profile-row">
      <summary>
        <span>
          <strong>{manifest.name}</strong>
          <small>
            {manifest.manufacturer_name} · {manifest.rules.length}{" "}
            {manifest.rules.length === 1 ? "rule" : "rules"} · Version{" "}
            {record.version_hash.slice(0, 10)}
          </small>
        </span>
        <span className={`manufacturer-profile-state is-${status}`}>
          {status === "approved"
            ? "Company-reviewed"
            : status === "revoked"
              ? "Revoked"
              : "Proposed"}
        </span>
      </summary>
      <div className="manufacturer-profile-content">
        {manifest.review && (
          <p className="manufacturer-profile-reviewer">
            Reviewed by {manifest.review.reviewer_id} ·{" "}
            {when(manifest.review.reviewed_at)}
          </p>
        )}
        {record.revocation && (
          <p className="manufacturer-profile-revoked">
            Revoked
            {record.revocation.revoked_at || record.revocation.created_at
              ? ` · ${when(record.revocation.revoked_at || record.revocation.created_at!)}`
              : ""}
            : {record.revocation.reason || "This version is no longer active."}
          </p>
        )}
        <div className="manufacturer-profile-rules">
          {manifest.rules.map((rule) => (
            <article key={rule.rule_id}>
              <div>
                <strong>{rule.device_tag}</strong>
                <span>{rule.label}</span>
              </div>
              {rule.when?.length ? (
                <small>
                  Applies when{" "}
                  {rule.when
                    .map(
                      (c) =>
                        `${c.field.replaceAll("_", " ")} = ${c.equals}${c.unit ? ` ${c.unit}` : ""}`,
                    )
                    .join(" and ")}
                </small>
              ) : null}
              <p>{manufacturerRuleDescription(rule)}</p>
              {status === "approved" && rule.status !== "approved" && (
                <small>Not active in this version.</small>
              )}
            </article>
          ))}
        </div>
        {!!manifest.aliases.length && (
          <div className="manufacturer-profile-aliases">
            <h4>Same-device names</h4>
            {manifest.aliases.map((alias) => (
              <p key={alias.alias_id}>
                {alias.tags.join(" · ")}
                <small>Physical device: {alias.physical_device_id}</small>
              </p>
            ))}
          </div>
        )}
        <div className="manufacturer-profile-sources">
          <h4>Source evidence</h4>
          {manifest.sources.map((source, index) => (
            <article key={`${source.document_id}-${index}`}>
              <div>
                <strong>{source.name}</strong>
                <span>
                  Rev {source.revision} · {manufacturerSourceLocation(source)}
                </span>
                {manufacturerSourcePath(
                  record.source_urls?.[source.document_id],
                ) && (
                  <button
                    className="text-button"
                    onClick={() => void openSource(record, source)}
                  >
                    Open source <ArrowUpRight size={14} />
                  </button>
                )}
              </div>
              {typeof source.location.text === "string" && (
                <blockquote>{source.location.text}</blockquote>
              )}
            </article>
          ))}
        </div>
        {admin && status === "proposed" && (
          <form
            className="manufacturer-profile-review"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!checked) return;
              if (
                await change(
                  `/equipment/profiles/${record.id}/review`,
                  { expected_version_hash: record.version_hash },
                  "Company review recorded for the entire profile. It can now be selected on an order.",
                )
              )
                setChecked(false);
            }}
          >
            <label>
              <input
                type="checkbox"
                checked={checked}
                onChange={(e) => setChecked(e.target.checked)}
              />
              I checked the source evidence, every rule and every same-device
              name in this profile.
            </label>
            <button className="primary" disabled={!checked || busy}>
              Review entire profile &amp; enable
            </button>
          </form>
        )}
        {admin &&
          status === "approved" &&
          (revoking ? (
            <form
              className="manufacturer-profile-revoke"
              onSubmit={async (e) => {
                e.preventDefault();
                if (
                  await change(
                    `/equipment/profiles/${record.id}/revoke`,
                    { reason: reason.trim() },
                    "This profile version is revoked. Its rules and aliases are no longer active.",
                  )
                )
                  setRevoking(false);
              }}
            >
              <label>
                Why revoke this version?
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  required
                  minLength={8}
                  maxLength={1000}
                  placeholder="For example, the design guide has been superseded"
                />
              </label>
              <div>
                <button
                  className="secondary"
                  disabled={busy || reason.trim().length < 8}
                >
                  Revoke version
                </button>
                <button
                  type="button"
                  className="text-button"
                  onClick={() => setRevoking(false)}
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <button
              className="text-button manufacturer-revoke-link"
              onClick={() => setRevoking(true)}
            >
              Revoke this version
            </button>
          ))}
      </div>
    </details>
  );
}
