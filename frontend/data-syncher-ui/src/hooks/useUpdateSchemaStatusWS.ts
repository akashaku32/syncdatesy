import useWebSocket from "react-use-websocket";

import { getWebSocketUrl } from "@/helpers/websocket";
import { SchemaStatusResponse } from "@/types/connectors";

import { useQueryClient } from "@tanstack/react-query";

interface SchemaStatusCache extends SchemaStatusResponse {
  last_updated?: string;
  _updateId?: number;
}

export const useUpdateSchemaStatusWS = (connectionId: number | null) => {
  const queryClient = useQueryClient();

  const isValidId =
    connectionId !== null && !isNaN(connectionId) && connectionId !== 0;

  const socketUrl = getWebSocketUrl(
    isValidId ? `/ws/update_schema_status/${connectionId}/` : "",
  );

  useWebSocket(socketUrl, {
    onOpen: () => {},
    onMessage: (event) => {
      try {
        const message = JSON.parse(event.data);
        const connectionIdNum = Number(connectionId);

        if (!connectionIdNum) return;

        // The message might be the data itself or wrapped in a data property
        const statusData = message.data || message;

        // Ensure we have the critical field
        if (statusData.is_in_progress !== undefined) {
          queryClient.setQueryData(
            ["SchemaStatus", connectionIdNum],
            (oldData: SchemaStatusCache | undefined) => {
              const newData = {
                ...(oldData || { is_in_progress: false, current_job: null }),
                ...statusData,
                last_updated: new Date().toISOString(),
                _updateId: Math.random(),
              };
              return newData;
            },
          );

          // If job completes, refresh all related data
          if (statusData.is_in_progress === false) {
            // Small delay to ensure DB is updated
            setTimeout(() => {
              const keysToInvalidate = [
                ["ReverseSchema", connectionIdNum],
                ["ConnectorTable", connectionIdNum],
                ["TableStatus", connectionIdNum],
                ["SchemaStatus", connectionIdNum],
                ["SelectedTables", connectionIdNum],
              ];

              keysToInvalidate.forEach((queryKey) => {
                queryClient.invalidateQueries({
                  queryKey,
                  refetchType: "active",
                });
              });
            }, 500);
          } else {
            // Even if in progress, invalidate to ensure UI sees the update
            queryClient.invalidateQueries({
              queryKey: ["SchemaStatus", connectionIdNum],
              refetchType: "active",
            });
          }
        }
      } catch (e) {
        console.error("[WS Schema Status] Parse error:", e);
      }
    },
    onError: (error) => {
      console.error(
        `[WS Schema Status] Error for connection ${connectionId}:`,
        error,
      );
    },
    onClose: () => {},
    shouldReconnect: () => true,
    reconnectInterval: 3000,
    reconnectAttempts: 10,
    share: true,
    retryOnError: true,
  });
};

export default useUpdateSchemaStatusWS;
