import { Authentication } from "./Auth";
import React from "react";
import ReactDOM from "react-dom/client";
import { IconContext } from "@phosphor-icons/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource/ibm-plex-mono/latin-400.css";
import App from "./Root";
import "./fonts.css";
import "./styles.css";
import "./app-refinement.css";
const client = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <IconContext.Provider value={{ size: 18, weight: "regular" }}>
        <Authentication>
          <App />
        </Authentication>
      </IconContext.Provider>
    </QueryClientProvider>
  </React.StrictMode>,
);
