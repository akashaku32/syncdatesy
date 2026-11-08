import { useCallback, useEffect, useMemo, useRef } from "react";

import useWebSocket from "react-use-websocket";

import { getWebSocketUrl } from "@/helpers/websocket";

import { useQueryClient } from "@tanstack/react-query";

export const useConnectionActivityLogWS = (connectionId: number | null) => {
  const queryClient = useQueryClient();
  const refreshTimerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    },
    [connectionId],
  );

  const getSocketUrl = useCallback(() => {
    const socketUrl = getWebSocketUrl(
      connectionId ? `/ws/connection_activity_log/${connectionId}/` : "",
    );

    if (!socketUrl) {
      throw new Error("WebSocket authentication token is not available.");
    }

    return socketUrl;
  }, [connectionId]);

  const onMessage = useCallback(
    (event: WebSocketEventMap["message"]) => {
      if (!connectionId) return;

      let message: { logs?: unknown };
      try {
        message = JSON.parse(event.data) as { logs?: unknown };
      } catch {
        return;
      }

      if (!Array.isArray(message.logs) || refreshTimerRef.current !== null) {
        return;
      }

      // The WS payload is an unfiltered one-hour snapshot, while each Overview
      // query can have different day and status filters. Refetch the active
      // filtered API query instead of merging incompatible snapshots. Coalesce
      // bursts because multiple backend events can contain the same snapshot.
      refreshTimerRef.current = window.setTimeout(() => {
        refreshTimerRef.current = null;
        void queryClient.invalidateQueries({
          queryKey: ["connectorActivity", Number(connectionId)],
          exact: false,
          refetchType: "active",
        });
      }, 250);
    },
    [connectionId, queryClient],
  );

  const options = useMemo(
    () => ({
      onOpen: () => {},
      onMessage,
      onError: () => {},
      onClose: () => {},
      shouldReconnect: (closeEvent: CloseEvent) => closeEvent.code !== 1000,
      reconnectInterval: 3000,
      reconnectAttempts: 10,
      share: true,
      retryOnError: true,
    }),
    [onMessage],
  );

  useWebSocket(connectionId ? getSocketUrl : null, options);
};

export default useConnectionActivityLogWS;
