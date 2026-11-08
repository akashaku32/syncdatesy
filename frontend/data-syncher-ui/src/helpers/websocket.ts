import { getAccessToken as getStoredAccessToken } from "@/lib/auth/token-cookies";

export const getAccessToken = (): string | null => {
  return getStoredAccessToken();
};

export const getWebSocketUrl = (path: string): string | null => {
  if (!path) return null;

  const envWs =
    (import.meta.env.VITE_WS_ORIGIN as string | undefined) ||
    (import.meta.env.VITE_API_ORIGIN as string | undefined) ||
    (import.meta.env.VITE_WS_URL as string | undefined);

  let socketBaseUrl = "";

  if (envWs) {
    const u = new URL(
      envWs.replace(/^ws:/, "http:").replace(/^wss:/, "https:"),
    );
    u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
    socketBaseUrl = u.origin;
  } else if (
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
  ) {
    socketBaseUrl = "wss://gcp.datasyncher.com";
  } else {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socketBaseUrl = `${protocol}//${window.location.host}`;
  }

  const token = getAccessToken();

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  if (!token) return null;

  const socketUrl = new URL(normalizedPath, `${socketBaseUrl}/`);
  socketUrl.searchParams.set("token", token);

  return socketUrl.toString();
};
