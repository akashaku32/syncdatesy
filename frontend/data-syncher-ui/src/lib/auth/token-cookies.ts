import Cookies from "js-cookie";

export const AUTH_COOKIE_DOMAIN = "gcp.datasyncher.com";

const isGcpDomain =
  typeof window !== "undefined" &&
  (window.location.hostname === AUTH_COOKIE_DOMAIN ||
    window.location.hostname.endsWith(`.${AUTH_COOKIE_DOMAIN}`));

const TOKEN_COOKIE_OPTIONS = {
  expires: 7,
  secure:
    typeof window !== "undefined" && window.location.protocol === "https:",
  sameSite: "strict" as const,
  ...(isGcpDomain ? { domain: AUTH_COOKIE_DOMAIN } : {}),
};

/** Remove both host-only and domain-scoped cookie variants. */
const removeTokenCookies = () => {
  Cookies.remove("access_token");
  Cookies.remove("refresh_token");
  if (isGcpDomain) {
    Cookies.remove("access_token", { domain: AUTH_COOKIE_DOMAIN });
    Cookies.remove("refresh_token", { domain: AUTH_COOKIE_DOMAIN });
  }
};

export const getAccessToken = (): string | null =>
  // Prefer localStorage — domain vs host-only cookies can leave a stale value
  // that Cookies.get returns first and that then overwrites a fresh Bearer header.
  localStorage.getItem("access_token") ?? Cookies.get("access_token") ?? null;

export const getRefreshToken = (): string | null =>
  localStorage.getItem("refresh_token") ?? Cookies.get("refresh_token") ?? null;

export const setAuthTokens = (accessToken: string, refreshToken: string) => {
  removeTokenCookies();
  Cookies.set("access_token", accessToken, TOKEN_COOKIE_OPTIONS);
  Cookies.set("refresh_token", refreshToken, TOKEN_COOKIE_OPTIONS);
  localStorage.setItem("access_token", accessToken);
  localStorage.setItem("refresh_token", refreshToken);
};

export const clearAuthTokens = () => {
  removeTokenCookies();
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
};
