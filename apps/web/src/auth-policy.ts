/** Deployed private surfaces require Clerk even if a server is misconfigured as local. */
export function allowsAuthenticationMode(
  mode: string,
  development: boolean,
): boolean {
  return mode === "clerk" || (development && mode === "local");
}
