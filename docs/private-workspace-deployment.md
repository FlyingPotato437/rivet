# Private workspace deployment

Rivet has three separate frontend builds. The normal production site remains a public product description and pilot booking page; it does not expose the workspace or its sign-in code.

| Surface | Build from repository root | Artifact | Hosting configuration |
| --- | --- | --- | --- |
| Public website | `npm run build` | `apps/web/dist` | Existing `vercel.json` |
| Authenticated workspace | `npm run build:workspace` | `apps/web/dist-workspace` | `apps/web/vercel.workspace.json` |
| Outlook task pane | `npm run build:outlook` | `apps/web/dist-outlook` | `apps/web/vercel.outlook.json` |

The workspace build uses Vite's explicit `workspace` mode. An empty workspace URL opens **Review**; order, customer, and source links retain their destinations through sign-in. The development server still supports both public and private routes. A deployed private surface requires Clerk authentication and rejects a development/local-auth API configuration.

## Configure a separate private host

1. Create a separate frontend project with root directory `apps/web`. Keep the existing public site's project and domain unchanged.
2. Apply `vercel.workspace.json` to this project explicitly; Vercel does not automatically select an alternate filename. Use its install command, `npm run build:workspace`, and output directory `dist-workspace`, including the SPA fallback and response headers. Do not rename or overwrite the repository's public `vercel.json`.
3. Set the build-time `VITE_API_URL` to the approved, HTTPS production API origin, without `/api` or another path. No model, Graph, database, or Clerk secret belongs in this frontend environment.
4. Configure the API with `RIVET_ENV=production`, `RIVET_AUTH_MODE=clerk`, production Clerk keys from the same instance, Organizations enabled, and the exact workspace origin in `RIVET_ALLOWED_ORIGINS`. The API's production startup validation also requires persistent storage and the other settings in [deployment.md](deployment.md).
5. Add the exact workspace and Outlook origins to the applicable Clerk domain/redirect settings and API allowlists. Invite approved pilot users into the correct organization. Merely knowing a workspace URL does not grant access to an order.
6. In the **Outlook deployment**, set `VITE_WORKSPACE_URL` to this verified private workspace origin and rebuild the pane. Otherwise its order/source links fall back to the task-pane origin. Keep `VITE_API_URL` empty for this build: the pane's `/api/*` requests must pass through a same-origin reverse proxy for its partitioned session cookie. The checked-in Outlook configuration proxies to `https://api.rivet-usa.com`; generate the configuration below if deploying another API origin. Never point the pane's workspace link at the public marketing host.

## Prepare an isolated release

Build from the repository root with the intended public origins. These commands
leave the running local demo and its development manifest intact:

```sh
VITE_CLERK_PUBLISHABLE_KEY=pk_live_YOUR_PUBLIC_KEY VITE_API_URL=https://api.rivet-usa.com npm run build:workspace
VITE_CLERK_PUBLISHABLE_KEY=pk_live_YOUR_PUBLIC_KEY VITE_API_URL= VITE_WORKSPACE_URL=https://app.rivet-usa.com npm run build:outlook
.venv/bin/python -m scripts.prepare_pilot_deployment \
  --workspace-origin https://app.rivet-usa.com \
  --api-origin https://api.rivet-usa.com \
  --outlook-origin https://outlook.rivet-usa.com \
  --output "$HOME/.codex/rivet-pilot-deployments/production-release"
```

The output contains separate `workspace` and `outlook` static deployment folders,
a production-origin Outlook manifest, a runtime-only backend source archive and
a server configuration template. It excludes local databases, customer documents,
credentials, source maps and recordings. The API rewrite disables response caching;
the workspace remains protected from embedding. The script refuses to overwrite
an existing release directory. Deploy each static folder to its own Vercel project.
Stage outside the repository: Vercel's monorepo discovery can otherwise inherit
the public site's build and embedding policy. The packager rejects build assets
containing development keys or server secrets. Supply only the production public
key at build time; production sign-in itself reads its configuration from the API.

Fill the private server environment separately. `RIVET_WORKSPACE_ENABLED=false`
and `RIVET_AGENT_AUTORUN=false` are initial defaults; open workspace access only
after production keys, domains and tenant isolation have been verified. Enable
automation only after the selected mailbox and a processing budget are agreed.
The production Compose configuration forwards the workspace flag explicitly.

Choose and verify the actual hostnames before deployment; the repository does not provision domains or prove a host is live. The private workspace's static JavaScript remains publicly downloadable like any browser app; authentication and tenant checks protect the data and actions. The task pane has its own embedding policy, while the full workspace intentionally cannot be embedded.

## Verify before inviting a pilot

- A signed-out workspace visitor sees Clerk sign-in. A user without a team cannot access order data. Reloading a source deep link preserves the order and source after sign-in.
- Authenticated API requests from both approved frontend origins succeed; another team's records and unauthenticated requests remain inaccessible.
- On the public site, `/pilot` still opens the supplied booking calendar, and `/#review` or an order URL still shows the private-pilot page rather than sign-in or workspace content.
- An Outlook source link opens the private workspace and exact cited page. Complete the actual Outlook acceptance checks in [outlook-addin.md](outlook-addin.md), including the message-thread, attachment, and host-authentication tests.

Build-isolation checks (after building both public and workspace artifacts):

```sh
node --test tests/frontend-public-build.test.mjs tests/frontend-workspace-build.test.mjs
```

These checks confirm release separation and the production authentication boundary. They do not substitute for deployed sign-in, tenant isolation, or live Outlook acceptance tests.
