import { useCallback, useMemo } from "react";

import useWebSocket from "react-use-websocket";

import { getWebSocketUrl } from "@/helpers/websocket";

import { useQueryClient } from "@tanstack/react-query";

interface CompanyConnectionsStatusMessage {
  event?: string;
  cmp_id?: number;
  connection_id?: number;
  error_message?: string | null;
}

export const useCompanyConnectionsStatusWS = (cmpId: number | null) => {
  const queryClient = useQueryClient();

  const socketUrl = getWebSocketUrl(
    cmpId ? `/ws/company_connections_status/${cmpId}/` : "",
  );

  const onMessage = useCallback(
    (event: WebSocketEventMap["message"]) => {
      if (!cmpId) return;

      let message: CompanyConnectionsStatusMessage;
      try {
        message = JSON.parse(event.data) as CompanyConnectionsStatusMessage;
      } catch {
        return;
      }

      // Invalidate if it's a generic migration status change OR a specific connection update
      if (
        message.event === "migration_status_changed" ||
        message.connection_id
      ) {
        // Wait 2 seconds before refetching to ensure the backend has committed the status to the DB
        setTimeout(() => {
          void queryClient.invalidateQueries({
            queryKey: ["connectors"],
          });
        }, 2000);
      }
    },
    [cmpId, queryClient],
  );

  const onError = useCallback(
    (_error: WebSocketEventMap["error"]) => {
      if (!cmpId) return;
    },
    [cmpId],
  );

  const onClose = useCallback(
    (_event: WebSocketEventMap["close"]) => {
      if (!cmpId) return;
    },
    [cmpId],
  );

  const options = useMemo(
    () => ({
      onOpen: () => {},
      onMessage,
      onError,
      onClose,
      shouldReconnect: (closeEvent: CloseEvent) => closeEvent.code !== 1000,
      reconnectInterval: 3000,
      reconnectAttempts: 10,
      share: true,
      retryOnError: true,
    }),
    [onMessage, onError, onClose],
  );

  useWebSocket(socketUrl, options);
};

export default useCompanyConnectionsStatusWS;
