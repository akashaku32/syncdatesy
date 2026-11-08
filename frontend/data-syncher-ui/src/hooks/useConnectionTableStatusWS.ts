import useWebSocket from "react-use-websocket";

import { getWebSocketUrl } from "@/helpers/websocket";
import { ConnectorSelectedTable } from "@/types/connectors";

import { useQueryClient } from "@tanstack/react-query";

interface TableStatusCache {
  tables: ConnectorSelectedTable[];
  schema_refresh_in_progress?: boolean;
  readable_time_frequency?: string | null;
  next_sync_time?: string | null;
  last_updated?: string;
  _updateId?: number;
}

interface RawTableStatus {
  table_name: string;
  status: string;
}

export const useConnectionTableStatusWS = (connectionId: number | null) => {
  const queryClient = useQueryClient();

  const isValidId =
    connectionId !== null && !isNaN(connectionId) && connectionId !== 0;

  const socketUrl = getWebSocketUrl(
    isValidId ? `/ws/connection_table_status/${connectionId}/` : "",
  );

  useWebSocket(socketUrl, {
    onOpen: () => {},
    onMessage: (event) => {
      try {
        const message = JSON.parse(event.data);
        const connectionIdNum = Number(connectionId);

        if (!connectionIdNum) return;

        // The message might be the data itself or wrapped in a data property
        const data = message.data || message;

        if (
          data.table_statuses ||
          data.schema_refresh_in_progress !== undefined ||
          data.next_sync_time !== undefined ||
          data.readable_time_frequency !== undefined
        ) {
          queryClient.setQueryData(
            ["TableStatus", connectionIdNum],
            (oldData: TableStatusCache | undefined) => {
              const currentTables = oldData?.tables || [];
              const newTables = data.table_statuses
                ? data.table_statuses.map(
                    (item: RawTableStatus, index: number) => ({
                      tbl_id: index,
                      table: item.table_name,
                      sequence: index,
                      status:
                        (item.status as
                          | "in_progress"
                          | "completed"
                          | "failed") || null,
                    }),
                  )
                : currentTables;

              const newData = {
                ...(oldData || { tables: [] }),
                tables: newTables,
                schema_refresh_in_progress:
                  data.schema_refresh_in_progress ??
                  oldData?.schema_refresh_in_progress ??
                  false,
                readable_time_frequency:
                  data.readable_time_frequency !== undefined
                    ? data.readable_time_frequency
                    : oldData?.readable_time_frequency,
                next_sync_time:
                  data.next_sync_time !== undefined
                    ? data.next_sync_time
                    : oldData?.next_sync_time,
                last_updated: new Date().toISOString(),
                _updateId: Math.random(),
              };

              return newData;
            },
          );

          // Sync SchemaStatus if table status indicates refresh is complete
          if (data.schema_refresh_in_progress === false) {
            queryClient.setQueryData(
              ["SchemaStatus", connectionIdNum],
              (old: unknown) => ({
                ...(typeof old === "object" && old !== null ? old : {}),
                is_in_progress: false,
                current_job: null,
                last_updated: new Date().toISOString(),
                _updateId: Math.random(),
              }),
            );

            // Refresh everything related to the schema refresh
            setTimeout(() => {
              const keys = [
                ["SchemaStatus", connectionIdNum],
                ["TableStatus", connectionIdNum],
                ["ReverseSchema", connectionIdNum],
                ["ConnectorTable", connectionIdNum],
                ["SelectedTables", connectionIdNum],
              ];
              keys.forEach((queryKey) => {
                queryClient.invalidateQueries({
                  queryKey,
                  refetchType: "active",
                });
              });
            }, 500);
          } else {
            // Invalidate TableStatus to ensure UI updates
            queryClient.invalidateQueries({
              queryKey: ["TableStatus", connectionIdNum],
              refetchType: "active",
            });
          }
        }
      } catch (e) {
        console.error(
          `[WS Table Status] Parse error for connection ${connectionId}:`,
          e,
        );
      }
    },
    onError: (error) => {
      console.error(
        `[WS Table Status] Error for connection ${connectionId}:`,
        error,
      );
    },
    onClose: () => {},
    shouldReconnect: (closeEvent) => closeEvent.code !== 1000,
    reconnectInterval: 3000,
    reconnectAttempts: 10,
    share: true,
    retryOnError: true,
  });
};

export default useConnectionTableStatusWS;
