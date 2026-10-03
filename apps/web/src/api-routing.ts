/** Resolve only Rivet API paths. Never forward a session token to another URL. */
export function createApiRouting(base: string, frontendOrigin: string) {
  const frontend = new URL(frontendOrigin).origin;
  const configured = base.trim().replace(/\/+$/, "");
  const backend = configured ? new URL(configured) : new URL(frontend);
  if (
    !["http:", "https:"].includes(backend.protocol) ||
    backend.username || backend.password ||
    backend.pathname !== "/" || backend.search || backend.hash
  ) throw new Error("VITE_API_URL must be an HTTP(S) origin, without a path.");
  if (frontend.startsWith("https:") && backend.protocol !== "https:")
    throw new Error("An HTTPS workspace requires an HTTPS API.");

  function isApiUrl(path: string) {
    try {
      const url = new URL(path, frontend);
      return !url.username && !url.password && !url.hash &&
        (url.origin === frontend || url.origin === backend.origin) &&
        url.pathname.startsWith("/api/");
    } catch { return false; }
  }
  function apiUrl(path: string) {
    if (!isApiUrl(path)) throw new Error("The requested URL is not a Rivet API endpoint.");
    const url = new URL(path, frontend);
    return backend.origin + url.pathname + url.search;
  }
  return { apiUrl, isApiUrl };
}
