import { useCallback, useMemo } from "react";

import useWebSocket from "react-use-websocket";

import {
  isTerminalOverallStatus,
  isTransientMigrationMessage,
  patchActivityLogForMigration,
  resolveCompletionMessage,
} from "@/helpers/activityLog";
import { getWebSocketUrl } from "@/helpers/websocket";
import { ConnectorActivityDetailResponse } from "@/types/connectors";

import { useQueryClient } from "@tanstack/react-query";

interface MigrationWSMessage {
  migration_session_id?: number | string;
  connection_id?: number | string;
  table_name?: string;
  status?: string;
  status_icon?: string;
  overall_status?: string;
  overall_status_icon?: string;
  staging_records_count?: number;
  start_time?: string | null;
  end_time?: string | null;
  error_message?: string;
  message?: string;
  timestamp?: string;
  job_level_message?: string | null;
  tables?: ConnectorActivityDetailResponse["tables"];
  logs?: ConnectorActivityDetailResponse["logs"];
}

const normalizeMigrationStatus = (status: string | undefined): string => {
  const normalized = (status || "").trim().toLowerCase();

  if (
    normalized === "s" ||
    normalized.includes("success") ||
    normalized.includes("completed")
  ) {
    return "completed";
  }

  if (
    normalized === "e" ||
    normalized === "f" ||
    normalized.includes("failed") ||
    normalized.includes("error")
  ) {
    return "failed";
  }

  if (
    normalized === "i" ||
    normalized === "running" ||
    normalized.includes("progress")
  ) {
    return "in_progress";
  }

  return normalized;
};

export const useMigrationStatusWS = (
  migrationId: number | null,
  connectionId: number | null,
) => {
  const queryClient = useQueryClient();

  const getSocketUrl = useCallback(() => {
    const socketUrl = getWebSocketUrl(
      migrationId ? `/ws/migration_status/${migrationId}/` : "",
    );

    if (!socketUrl) {
      throw new Error("WebSocket authentication token is not available.");
    }

    return socketUrl;
  }, [migrationId]);

  const onMessage = useCallback(
    (event: WebSocketEventMap["message"]) => {
      if (!migrationId || !connectionId) return;

      let message: MigrationWSMessage;
      try {
        message = JSON.parse(event.data) as MigrationWSMessage;
      } catch {
        return;
      }

      const numericMigrationId = Number(migrationId);
      const numericConnectionId = Number(connectionId);

      // Session IDs can collide across connections. The backend currently
      // broadcasts those scoped payloads through one session-only WS group.
      if (Number(message.connection_id) !== numericConnectionId) {
        return;
      }

      if (Number(message.migration_session_id) !== numericMigrationId) {
        return;
      }

      queryClient.setQueryData(
        [
          "connectorActivityDetails",
          numericMigrationId,
          numericConnectionId,
          undefined,
        ],
        (oldData: ConnectorActivityDetailResponse | undefined) => {
          // Spread top-level fields but handle 'tables' separately to avoid overwriting
          const { tables: _wsTablesIgnored, ...messageWithoutTables } = message;
          const updated = {
            ...(oldData || { tables: [] }),
            ...messageWithoutTables,
          };

          // Preserve existing tables from cache; if WS sends a full tables list,
          // deep-merge each entry to retain start_time / end_time from cached data.
          if (message.tables && message.tables.length > 0) {
            const existingTables = oldData?.tables || [];
            updated.tables = message.tables.map((wsTable) => {
              const cached = existingTables.find(
                (t) =>
                  t.table_name.toLowerCase() ===
                  wsTable.table_name.toLowerCase(),
              );
              // Merge: cached data first, then WS data on top — preserving times if WS omits them
              return {
                ...(cached || {}),
                ...wsTable,
                start_time:
                  wsTable.start_time !== undefined &&
                  wsTable.start_time !== null
                    ? wsTable.start_time
                    : (cached?.start_time ?? null),
                end_time:
                  wsTable.end_time !== undefined && wsTable.end_time !== null
                    ? wsTable.end_time
                    : (cached?.end_time ?? null),
              };
            });
          } else if (!message.tables && oldData?.tables) {
            updated.tables = [...oldData.tables];
          }

          if (message.table_name && updated.tables) {
            const tableName = message.table_name;
            const tableIndex = updated.tables.findIndex(
              (t: { table_name: string }) =>
                t.table_name.toLowerCase() === tableName.toLowerCase(),
            );

            if (tableIndex !== -1) {
              const updatedTable = { ...updated.tables[tableIndex] };

              if (message.staging_records_count !== undefined) {
                updatedTable.staging_records_count =
                  message.staging_records_count;
              }
              if (message.status) {
                updatedTable.status = message.status;
              }
              if (message.status_icon) {
                updatedTable.status_icon = message.status_icon;
              }
              if (message.error_message) {
                updatedTable.error_message = message.error_message;
              }
              if (message.message) {
                updatedTable.message = message.message;
                if (!updatedTable.error_message) {
                  updatedTable.error_message = message.message;
                }
              }
              // Apply start_time / end_time from WS when explicitly provided
              if (message.start_time !== undefined) {
                updatedTable.start_time = message.start_time;
              }
              if (message.end_time !== undefined) {
                updatedTable.end_time = message.end_time;
              }

              updated.tables = [
                ...updated.tables.slice(0, tableIndex),
                updatedTable,
                ...updated.tables.slice(tableIndex + 1),
              ];
            }
            // Table not found in cache — skip update
          }

          if (message.message && !message.logs) {
            const logs = oldData?.logs || [];
            const isDuplicate = logs.some(
              (l) =>
                l.message === message.message &&
                l.timestamp === message.timestamp,
            );

            if (!isDuplicate) {
              const newLog = {
                message: message.message,
                timestamp: message.timestamp || new Date().toISOString(),
                table: message.table_name || "",
                status: message.status_icon || message.status || "I",
              };
              updated.logs = [newLog, ...logs];
            }
          }

          // Normalize status
          const derivedStatus = normalizeMigrationStatus(
            message.overall_status ||
              message.overall_status_icon ||
              message.status_icon ||
              message.status,
          );

          if (
            derivedStatus === "completed" ||
            derivedStatus === "failed" ||
            derivedStatus === "in_progress"
          ) {
            const isImplicitUpdate = !message.overall_status;
            const currentOverall = oldData?.overall_status || "";
            const currentIsFailed = currentOverall === "failed";
            const currentIsTerminal = isTerminalOverallStatus(currentOverall);

            if (
              isImplicitUpdate &&
              currentIsFailed &&
              derivedStatus === "in_progress"
            ) {
              updated.overall_status = "failed";
            } else if (
              isImplicitUpdate &&
              currentIsTerminal &&
              derivedStatus === "in_progress"
            ) {
              // Late table-level WS payloads must not downgrade a finished run.
              updated.overall_status = currentOverall;
            } else {
              updated.overall_status = derivedStatus;
            }
          }

          const resolvedOverall = (updated.overall_status || "").toLowerCase();
          const isTerminalOverall =
            resolvedOverall.includes("completed") ||
            resolvedOverall.includes("success") ||
            resolvedOverall.includes("failed") ||
            resolvedOverall.includes("error");

          if (isTerminalOverall) {
            const candidateMessage =
              message.job_level_message || message.message || undefined;
            const safeCandidate =
              candidateMessage && !isTransientMigrationMessage(candidateMessage)
                ? candidateMessage
                : undefined;
            const existingMessage = oldData?.job_level_message || "";
            const existingIsDetailed =
              existingMessage && !isTransientMigrationMessage(existingMessage);

            if (
              existingIsDetailed &&
              isTransientMigrationMessage(candidateMessage)
            ) {
              updated.job_level_message = existingMessage;
            } else if (
              !updated.job_level_message ||
              isTransientMigrationMessage(updated.job_level_message)
            ) {
              updated.job_level_message = resolveCompletionMessage(
                existingMessage,
                resolvedOverall.includes("failed") ||
                  resolvedOverall.includes("error"),
                safeCandidate,
              );
            }
          }

          return updated;
        },
      );

      const derivedOverall = (() => {
        const raw = (
          message.overall_status ||
          message.status_icon ||
          message.status ||
          ""
        ).toLowerCase();
        if (raw) return raw;
        const tables = message.tables || [];
        if (!tables.length) return "";
        const allDone = tables.every((table) => {
          const icon = (table.status_icon || table.status || "").toLowerCase();
          return (
            icon.includes("completed") ||
            icon.includes("success") ||
            icon.includes("failed") ||
            icon.includes("error")
          );
        });
        if (!allDone) return "";
        const anyFailed = tables.some((table) => {
          const icon = (table.status_icon || table.status || "").toLowerCase();
          return icon.includes("failed") || icon.includes("error");
        });
        return anyFailed ? "failed" : "completed";
      })();

      const terminalStatus = normalizeMigrationStatus(
        message.overall_status ||
          message.overall_status_icon ||
          message.status_icon ||
          message.status ||
          derivedOverall,
      );
      if (terminalStatus === "completed" || terminalStatus === "failed") {
        const completionMessage =
          message.job_level_message || message.message || undefined;
        const safeCompletionMessage =
          completionMessage && !isTransientMigrationMessage(completionMessage)
            ? completionMessage
            : undefined;

        patchActivityLogForMigration(
          queryClient,
          numericConnectionId,
          numericMigrationId,
          {
            overallStatus: terminalStatus,
            message: safeCompletionMessage,
          },
        );

        // Refresh details only. The activity list is patched optimistically and
        // protected from stale refetches via terminal patch storage.
        setTimeout(() => {
          void queryClient.invalidateQueries({
            queryKey: [
              "connectorActivityDetails",
              numericMigrationId,
              numericConnectionId,
              undefined,
            ],
          });
        }, 2000);
      }
    },
    [connectionId, migrationId, queryClient],
  );

  const onError = useCallback(
    (error: WebSocketEventMap["error"]) => {
      if (!migrationId || !connectionId) return;
      console.error(
        `[WS Migration Status] Error for connection ${connectionId}, migration ${migrationId}:`,
        error,
      );
    },
    [connectionId, migrationId],
  );

  const onClose = useCallback(
    (event: WebSocketEventMap["close"]) => {
      if (!migrationId || !connectionId) return;
      // Only log unexpected closes (not normal 1000/1005 no-op closes)
      if (event.code !== 1000 && event.code !== 1005) {
        console.error(
          `[WS Migration Status] Unexpected close for connection ${connectionId}, migration ${migrationId}: Code ${event.code}`,
        );
      }
    },
    [connectionId, migrationId],
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

  useWebSocket(migrationId && connectionId ? getSocketUrl : null, options);
};

export default useMigrationStatusWS;
