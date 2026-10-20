import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, when } from "./api";
import { useAccount } from "./Auth";
import { ErrorNote } from "./ui";
import {
  manufacturerProfileStatus,
  type ManufacturerProfileRecord,
} from "./manufacturer-profile-types";
import "./manufacturer-profile-settings.css";

type OrderProfile = {
  order_id: string;
  version: number;
  profile_version_id: string | null;
  profile: ManufacturerProfileRecord | null;
  issues: string[];
};

/** Advanced order convention assignment; the normal work stays automatic. */
export function OrderManufacturerProfile({ id }: { id: string }) {
  const admin = useAccount().role === "org:admin";
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["equipment-profile", id],
    queryFn: () => api<OrderProfile>(`/orders/${id}/equipment/profile`),
    enabled: !!id,
  });
  const profiles = useQuery({
    queryKey: ["manufacturer-profiles"],
    queryFn: () =>
      api<{ profiles: ManufacturerProfileRecord[] }>("/equipment/profiles"),
    enabled: admin,
  });
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const current = query.data?.profile;
  const available = (profiles.data?.profiles || []).filter(
    (profile) => manufacturerProfileStatus(profile) === "approved",
  );

  return (
    <details className="order-manufacturer-profile">
      <summary>
        <span>Company rules</span>
        <strong>
          {current?.manifest.name ||
            (query.isPending ? "Loading…" : "No profile selected")}
        </strong>
      </summary>
      <div>
        {query.isError && (
          <ErrorNote message={(query.error as Error).message} />
        )}
        {error && <ErrorNote message={error} />}
        {notice && <p role="status">{notice}</p>}
        {current ? (
          <p>
            {manufacturerProfileStatus(current) === "approved"
              ? "Company-reviewed"
              : "Not active"}{" "}
            · Version {current.version_hash.slice(0, 10)}
            {current.manifest.review
              ? ` · ${current.manifest.review.reviewer_id}, ${when(current.manifest.review.reviewed_at)}`
              : ""}
          </p>
        ) : (
          <p>
            An optional company profile adds source-cited device names, part
            compatibility and layout constraints.
          </p>
        )}
        {query.data?.issues.map((issue, index) => (
          <p className="order-profile-issue" key={index}>
            {issue}
          </p>
        ))}
        {admin && query.data && !editing && (
          <button
            className="text-button"
            onClick={() => {
              setSelected(query.data!.profile_version_id || "");
              setReason("");
              setNotice("");
              setError("");
              setEditing(true);
            }}
          >
            Change profile
          </button>
        )}
        {admin && query.data && editing && (
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (busy || reason.trim().length < 8) return;
              setBusy(true);
              setError("");
              setNotice("");
              try {
                const result = await api<OrderProfile>(
                  `/orders/${id}/equipment/profile`,
                  {
                    expected_version: query.data!.version,
                    profile_version_id: selected || null,
                    reason: reason.trim(),
                  },
                  "PUT",
                );
                queryClient.setQueryData(["equipment-profile", id], result);
                await Promise.all([
                  queryClient.invalidateQueries({
                    queryKey: ["equipment", id],
                  }),
                  queryClient.invalidateQueries({
                    queryKey: ["equipment-corrections", id],
                  }),
                ]);
                setNotice(
                  selected
                    ? "Profile selected. Prepared work must match this version’s rules."
                    : "Company profile removed from this order.",
                );
                setEditing(false);
              } catch (e) {
                setError((e as Error).message);
                await query.refetch();
              } finally {
                setBusy(false);
              }
            }}
          >
            {profiles.isError && (
              <ErrorNote message={(profiles.error as Error).message} />
            )}
            <label>
              Company profile
              <select
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                disabled={busy || profiles.isPending}
              >
                <option value="">No company profile</option>
                {current &&
                  !available.some((profile) => profile.id === current.id) && (
                    <option value={current.id} disabled>
                      {current.manifest.name} · unavailable
                    </option>
                  )}
                {available.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.manifest.name} ·{" "}
                    {profile.version_hash.slice(0, 10)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Reason for this change
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                minLength={8}
                maxLength={1000}
                placeholder="For example, apply the reviewed Panel A design guide"
                disabled={busy}
              />
            </label>
            <div>
              <button
                className="secondary"
                disabled={
                  busy ||
                  profiles.isPending ||
                  reason.trim().length < 8 ||
                  selected === (query.data.profile_version_id || "")
                }
              >
                {busy ? "Saving…" : "Save profile"}
              </button>
              <button
                type="button"
                className="text-button"
                onClick={() => setEditing(false)}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
            {!available.length && !profiles.isPending && (
              <p>
                Add and review a manufacturer profile in Settings before
                selecting it here.
              </p>
            )}
          </form>
        )}
      </div>
    </details>
  );
}
