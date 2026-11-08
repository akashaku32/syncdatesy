import React, { useEffect, useRef, useState } from "react";

import { Box, Button, Text } from "@chakra-ui/react";

import { FcGoogle } from "react-icons/fc";

import axios from "axios";

const FORM_VALUES_SESSION_KEY = "gdrive_form_values";

export interface GoogleDriveTokens {
  access_token: string;
  refresh_token: string;
  token_expires_at?: string;
  folder_id?: string;
  folder_name?: string;
}

interface GoogleDriveOAuthProps {
  /** Values the user has already typed: connection_name, destination_schema, etc. */
  formValues: Record<string, string>;
  /** Resolved client_id value (already extracted from formValues by the parent) */
  clientId: string;
  /** Resolved client_secret value (already extracted from formValues by the parent) */
  clientSecret: string;
  /** Called once tokens are received — parent injects them into form state */
  onTokensReceived: (_tokens: GoogleDriveTokens) => void;
  /** Whether the client_id and client_secret fields are filled */
  canAuthorize: boolean;
}

type AuthStatus = "idle" | "redirecting" | "done" | "error";

type GoogleDriveAuthResponse = {
  auth_url?: string;
};

const env = import.meta.env;
const isLocalhost =
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1";
const GOOGLE_DRIVE_AUTH_PATH = "/api/v1/source/googledrive/authorize/";
const GOOGLE_DRIVE_CALLBACK_PATH = "/api/v1/source/googledrive/callback";
const GOOGLE_DRIVE_OAUTH_TOKENS_PATH =
  "/api/v1/source/googledrive/oauth-tokens/";

const stripTrailingSlash = (value: string) => value.replace(/\/+$/, "");

const configuredDjangoBase = env?.VITE_API_BASE_URL;
const baseURL = stripTrailingSlash(
  isLocalhost
    ? "https://gcp.datasyncher.com"
    : configuredDjangoBase || window.location.origin,
);
const REDIRECT_URI = `${baseURL}${GOOGLE_DRIVE_CALLBACK_PATH}`;

const isSensitiveOAuthField = (fieldName: string) => {
  const normalized = fieldName.toLowerCase().replace(/[\s\-_.]/g, "");
  return normalized.includes("clientsecret") || normalized.includes("secret");
};

const getPersistableFormValues = (values: Record<string, string>) =>
  Object.fromEntries(
    Object.entries(values).filter(
      ([fieldName]) => !isSensitiveOAuthField(fieldName),
    ),
  );

const GoogleDriveOAuth: React.FC<GoogleDriveOAuthProps> = ({
  formValues,
  clientId,
  clientSecret,
  onTokensReceived,
  canAuthorize,
}) => {
  const [status, setStatus] = useState<AuthStatus>("idle");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const handledRef = useRef(false); // prevent double-handling in StrictMode

  // Remove query params from the URL so a page refresh doesn't re-trigger
  const cleanUrl = () => {
    window.history.replaceState({}, document.title, window.location.pathname);
  };

  // ─── Step 2: On mount, redeem oauth_state or handle legacy query tokens ──
  useEffect(() => {
    const redeemOAuthState = async (oauthState: string) => {
      const response = await axios.get<GoogleDriveTokens>(
        `${baseURL}${GOOGLE_DRIVE_OAUTH_TOKENS_PATH}`,
        { params: { oauth_state: oauthState } },
      );
      return response.data;
    };

    const params = new URLSearchParams(window.location.search);
    const oauthState = params.get("oauth_state");
    const accessToken = params.get("access_token");
    const refreshToken = params.get("refresh_token");
    const tokenExpiresAt = params.get("token_expires_at") ?? undefined;
    const folderId = params.get("folder_id") ?? undefined;
    const folderName = params.get("folder_name") ?? undefined;
    const error = params.get("oauth_error") || params.get("error");

    if (oauthState || accessToken || refreshToken || error) {
      cleanUrl();
    }

    if (!oauthState && !accessToken && !refreshToken && !error) return;

    if (error) {
      setStatus("error");
      setErrorMsg("Google authorization was denied.");
      return;
    }

    if (handledRef.current) return;
    handledRef.current = true;

    const applyTokens = (tokens: GoogleDriveTokens) => {
      setStatus("done");
      onTokensReceived({
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token ?? "",
        token_expires_at: tokens.token_expires_at,
        folder_id: tokens.folder_id ?? folderId,
        folder_name: tokens.folder_name ?? folderName,
      });
    };

    if (oauthState) {
      redeemOAuthState(oauthState)
        .then(applyTokens)
        .catch(() => {
          handledRef.current = false;
          setStatus("error");
          setErrorMsg("Google authorization session expired. Please try again.");
        });
      return;
    }

    if (accessToken) {
      applyTokens({
        access_token: accessToken,
        refresh_token: refreshToken ?? "",
        token_expires_at: tokenExpiresAt,
        folder_id: folderId,
        folder_name: folderName,
      });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ─── Step 1: Build auth URL and redirect to Google ───────────────────────
  const handleAuthorize = async () => {
    if (!canAuthorize) return;

    setStatus("redirecting");
    setErrorMsg("");

    try {
      // Save form values before leaving the page
      const dataToSave = {
        ...getPersistableFormValues(formValues),
        client_id: clientId,
      };
      sessionStorage.setItem(
        FORM_VALUES_SESSION_KEY,
        JSON.stringify(dataToSave),
      );

      const response = await axios.get<GoogleDriveAuthResponse>(
        `${baseURL}${GOOGLE_DRIVE_AUTH_PATH}`,
        {
          params: {
            client_id: clientId,
            client_secret: clientSecret,
            redirect_uri: REDIRECT_URI,
          },
        },
      );

      const googleAuthUrl = response.data.auth_url;

      if (!googleAuthUrl) {
        throw new Error("Google authorization URL not received from backend.");
      }

      // Redirect the browser to Google's consent screen
      window.location.href = googleAuthUrl;
    } catch (err: unknown) {
      setStatus("error");
      const msg =
        err instanceof Error ? err.message : "Failed to start authorization.";
      setErrorMsg(msg);
    }
  };

  // ─── Render ───────────────────────────────────────────────────────────────
  if (status === "done") return null;

  return (
    <Box mt={4}>
      {status === "error" && (
        <Text fontSize="xs" color="red.500" mb={2}>
          {errorMsg || "Authorization failed. Please try again."}
        </Text>
      )}
      <Button
        w="full"
        variant="outline"
        bg="white"
        onClick={handleAuthorize}
        loading={status === "redirecting"}
        disabled={!canAuthorize || status === "redirecting"}
        title={
          !canAuthorize
            ? "Enter Client ID and Client Secret first"
            : "Authorize with Google"
        }
      >
        <FcGoogle />
        {status === "error" ? "Retry with Google" : "Authorize with Google"}
      </Button>
      {!canAuthorize && (
        <Text fontSize="xs" color="gray.500" mt={1}>
          Enter your Client ID and Client Secret above first.
        </Text>
      )}
    </Box>
  );
};

export default GoogleDriveOAuth;
