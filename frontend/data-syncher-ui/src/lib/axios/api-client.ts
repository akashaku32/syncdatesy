import axios, {
  AxiosError,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";

import { toaster } from "@/components/ui/toaster";
import { LONG_DESTINATION_AUTH_TIMEOUT_MS } from "@/constants/api-timeouts";
import ServerRoutes from "@/constants/server-routes";
import {
  clearAuthTokens,
  getAccessToken,
  getRefreshToken,
  setAuthTokens,
} from "@/lib/auth/token-cookies";
import { type ErrorResponseType } from "@/types/error";

export { LONG_DESTINATION_AUTH_TIMEOUT_MS };

const envApiOrigin = import.meta.env.VITE_API_ORIGIN as string | undefined;

/** Use the same loopback hostname as the page (localhost vs 127.0.0.1) to avoid CORS quirks. */
function resolveApiBase(): string {
  const trimmed = envApiOrigin?.replace(/\/$/, "").trim();
  if (!trimmed) {
    return "https://gcp.datasyncher.com";
  }

  if (typeof window === "undefined") {
    return trimmed;
  }

  try {
    const api = new URL(
      trimmed.includes("://") ? trimmed : `http://${trimmed}`,
    );
    const pageHost = window.location.hostname;
    const apiHost = api.hostname;
    const loopbackMismatch =
      (pageHost === "localhost" && apiHost === "127.0.0.1") ||
      (pageHost === "127.0.0.1" && apiHost === "localhost");
    if (loopbackMismatch) {
      api.hostname = pageHost;
      return api.origin.replace(/\/$/, "");
    }
  } catch {
    /* keep trimmed */
  }

  return trimmed;
}

/**
 * Use the configured API origin, falling back to the shared GCP backend.
 */
function getAxiosBaseURL(): string {
  const apiBase = resolveApiBase();
  return apiBase ? `${apiBase}/api/v1/` : "/api/v1/";
}

function getRefreshTokenURL(): string {
  const apiBase = resolveApiBase();
  return `${apiBase}/api/v1/${ServerRoutes.auth.refresh()}`;
}

const AxiosInstance = axios.create({
  baseURL: getAxiosBaseURL(),
  timeout: 30000,
});

const DEFAULT_AXIOS_TIMEOUT_MS = 30000;
const BUMPED_AXIOS_TIMEOUT_MS = 40000;

function requestUrl(config: InternalAxiosRequestConfig): string {
  const base = config.baseURL ?? "";
  const path = config.url ?? "";
  if (/^https?:\/\//i.test(path)) {
    return path;
  }
  return `${base}${path}`;
}

function isLongDestinationAuthRequest(
  config: InternalAxiosRequestConfig,
): boolean {
  const url = requestUrl(config);
  return (
    /destinations\/create(\/|\?|$)/.test(url) ||
    /destinations\/update\/[^/]+(\/|\?|$)/.test(url) ||
    /destinations\/test\/\d+/.test(url) ||
    /connection\/\d+\/test(\/|\?|$)/.test(url) ||
    /source-config\/[^/]+\/create(\/|\?|$)/.test(url)
  );
}

function shouldSuppressGlobalErrorToast(
  config: RetryableRequestConfig | undefined,
): boolean {
  const headers = config?.headers;
  if (!headers) {
    return false;
  }
  const flag =
    (headers as Record<string, unknown>)["X-Suppress-Global-Error-Toast"] ??
    (typeof headers.get === "function"
      ? headers.get("X-Suppress-Global-Error-Toast")
      : undefined);
  return flag === "1" || flag === "true";
}

function isDestinationConnectionTestRequest(url: string): boolean {
  return /destinations\/test\/\d+/.test(url);
}

function isDefaultAxiosTimeout(timeout: number | undefined): boolean {
  return (
    timeout === undefined ||
    timeout === DEFAULT_AXIOS_TIMEOUT_MS ||
    timeout === BUMPED_AXIOS_TIMEOUT_MS
  );
}

type RefreshTokenResponse = {
  access?: string;
  access_token?: string;
  refresh?: string;
  refresh_token?: string;
};

type RetryableRequestConfig = InternalAxiosRequestConfig & {
  _retry?: boolean;
  customToken?: string;
};

let refreshPromise: Promise<string> | null = null;

export const refreshAccessToken = async () => {
  if (refreshPromise) return refreshPromise;

  const refreshToken = getRefreshToken();

  if (!refreshToken) {
    throw new Error("Refresh token is not available.");
  }

  refreshPromise = axios
    .post<RefreshTokenResponse>(getRefreshTokenURL(), {
      refresh: refreshToken,
      refresh_token: refreshToken,
    })
    .then(({ data }) => {
      const accessToken = data.access_token ?? data.access;
      const nextRefreshToken =
        data.refresh_token ?? data.refresh ?? refreshToken;

      if (!accessToken) {
        throw new Error("Refresh response did not include an access token.");
      }

      setAuthTokens(accessToken, nextRefreshToken);

      return accessToken;
    })
    .finally(() => {
      refreshPromise = null;
    });

  return refreshPromise;
};

// Set Custom Headers
AxiosInstance.defaults.headers.common["Expires"] = "0";
AxiosInstance.defaults.headers.common["Cache-Control"] = "no-cache";
AxiosInstance.defaults.headers.common["Pragma"] = "no-cache";

AxiosInstance.interceptors.request.use(
  (config: RetryableRequestConfig): InternalAxiosRequestConfig => {
    const url = requestUrl(config);
    const isAuthCredentialRequest =
      /authentication\/?(\?|$)/.test(url) ||
      /authentication\/refresh/.test(url) ||
      /password-reset/.test(url) ||
      /verify-email/.test(url) ||
      /resend-otp/.test(url);

    // Login/register must not send a stale Bearer — and must not overwrite a
    // token already set on a 401-retry.
    const headers = config.headers;
    const existingAuth =
      headers?.Authorization ??
      headers?.authorization ??
      (typeof headers?.get === "function"
        ? headers.get("Authorization")
        : undefined);

    if (!isAuthCredentialRequest && !existingAuth) {
      const token = config.headers.customToken ?? getAccessToken();
      if (token) {
        config.headers["Authorization"] = `Bearer ${token}`;
      }
    }
    if (config.headers.customToken) {
      delete config.headers.customToken;
    }
    // Preserve per-request timeouts (e.g. transform dry-run).
    if (
      isLongDestinationAuthRequest(config) &&
      isDefaultAxiosTimeout(config.timeout)
    ) {
      config.timeout = LONG_DESTINATION_AUTH_TIMEOUT_MS;
    } else if (isDefaultAxiosTimeout(config.timeout)) {
      config.timeout = BUMPED_AXIOS_TIMEOUT_MS;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

AxiosInstance.interceptors.response.use(
  (response: AxiosResponse): AxiosResponse => response,
  async (error: AxiosError): Promise<AxiosResponse | ErrorResponseType> => {
    const original = error.config as RetryableRequestConfig | undefined;
    const errorData = error.response?.data as ErrorResponseType | undefined;

    if (errorData?.trial_expired && errorData?.redirect_to) {
      const redirectTo = errorData.redirect_to;

      toaster.error({
        title: "Trial Period Expired",
        description:
          errorData.message ||
          errorData.error ||
          "Your trial period has expired. Please select a subscription plan.",
      });

      setTimeout(() => {
        window.location.replace(redirectTo);
      }, 1000);

      return Promise.reject(errorData);
    }

    const reqUrl = `${original?.baseURL ?? ""}${original?.url ?? ""}`;
    const isAuthCredentialRequest =
      /authentication\/?(\?|$)/.test(reqUrl) ||
      /authentication\/refresh/.test(reqUrl);

    // Failed login must not trigger refresh+retry (that rotates tokens and
    // can leave a stale cookie Bearer on subsequent requests).
    if (
      error.response?.status === 401 &&
      original &&
      !original._retry &&
      !isAuthCredentialRequest
    ) {
      original._retry = true;

      try {
        const accessToken = await refreshAccessToken();
        original.headers = original.headers ?? {};
        original.headers["Authorization"] = `Bearer ${accessToken}`;

        return AxiosInstance(original);
      } catch (refreshError) {
        clearAuthTokens();
        return Promise.reject(refreshError);
      }
    }

    const suppressGlobalToast =
      shouldSuppressGlobalErrorToast(original) ||
      isDestinationConnectionTestRequest(reqUrl);

    if (!suppressGlobalToast) {
      if (error.code === "ECONNABORTED") {
        toaster.error({
          title: "Request Timeout",
          description: "The server took too long to respond. Try again soon.",
        });
      } else if (!error.response) {
        const devDetail =
          import.meta.env.DEV && error.message ? ` ${error.message}` : "";
        toaster.error({
          title: "Server Unreachable",
          description: import.meta.env.DEV
            ? `No response from API (use Vite proxy /api → Django on :8000). Is runserver up?${devDetail}`
            : "The server appears to be down or unreachable. Please try again later.",
        });
      }
    }
    return Promise.reject(error.response?.data ?? error);
  },
);

export default AxiosInstance;
