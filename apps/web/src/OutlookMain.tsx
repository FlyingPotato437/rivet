import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./styles.css";
import OutlookAssistant from "./OutlookAssistant";
import { OutlookSession } from "./OutlookSession";

const client = new QueryClient({ defaultOptions: { queries: { retry: 1 } } });
// Office must initialize even while sign-in is showing, not only after authentication.
const initializeOffice = () => {
  void window.Office?.onReady().catch(() => {});
};
if (window.Office) initializeOffice();
else
  document
    .querySelector('script[src*="appsforoffice.microsoft.com"]')
    ?.addEventListener("load", initializeOffice, { once: true });
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <OutlookSession>
        <OutlookAssistant />
      </OutlookSession>
    </QueryClientProvider>
  </React.StrictMode>,
);
