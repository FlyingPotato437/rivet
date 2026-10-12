import { useEffect, useState } from "react";
import Cal, { getCalApi } from "@calcom/embed-react";
import { ArrowUpRight, Clock, VideoCamera } from "@phosphor-icons/react";
import { PublicFooter, PublicHeader } from "./Landing";

export default function PilotPage() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let alive = true;
    getCalApi({ namespace: "rivet-pilot" })
      .then((cal) => {
        if (!alive) return;
        cal("ui", {
          theme: "dark",
          hideEventTypeDetails: false,
          layout: "month_view",
          cssVarsPerTheme: {
            dark: { "cal-brand": "#cab5eb" },
            light: { "cal-brand": "#6c4e8f" },
          },
        });
        cal("on", {
          action: "__iframeReady",
          callback: () => {
            if (alive) setReady(true);
          },
        });
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  return (
    <div className="rivet-public public-pilot">
      <PublicHeader booking />
      <main className="public-wrap">
        <div className="public-pilot-intro">
          <div>
            <p className="public-eyebrow">Request a pilot</p>
            <h1>
              Let’s look at
              <br />
              <span>your workflow.</span>
            </h1>
          </div>
          <div>
            <p>
              Meet with Srikanth, Rivet’s founder. We’ll walk through how your
              team handles returned drawings and see where Rivet can help.
            </p>
            <div className="public-call-details">
              <span>
                <Clock size={18} />
                30 minutes
              </span>
              <span>
                <VideoCamera size={19} />
                Video call
              </span>
            </div>
            <p className="public-pilot-note">
              No preparation required. A sample or redacted document is welcome
              if you have one.
            </p>
          </div>
        </div>
        <section
          className="public-calendar"
          aria-label="Book a Rivet pilot conversation"
        >
          {!ready && (
            <p className="public-calendar-loading" role="status">
              Choose a date and time in the calendar below.
            </p>
          )}
          <Cal
            namespace="rivet-pilot"
            calLink="srikanth-samy-pfuse0/30min"
            style={{ width: "100%", height: "100%", overflow: "auto" }}
            config={{ layout: "month_view", theme: "dark" }}
          />
        </section>
        <div className="public-calendar-footer">
          <p>
            Scheduling is provided by Cal.com. You’ll receive a calendar
            invitation after booking.
          </p>
          <a
            href="https://cal.com/srikanth-samy-pfuse0/30min"
            target="_blank"
            rel="noreferrer"
          >
            Open calendar in a new tab <ArrowUpRight size={16} />
          </a>
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}
