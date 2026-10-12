import React from "react";
import ReactDOM from "react-dom/client";
import { IconContext } from "@phosphor-icons/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/schibsted-grotesk/wght.css";
import "@fontsource/commit-mono/latin-400.css";
import App from "./Root";
import "./fonts.css";
const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <IconContext.Provider value={{ size: 18, weight: "regular" }}>
        <App />
      </IconContext.Provider>
    </QueryClientProvider>
  </React.StrictMode>,
);
