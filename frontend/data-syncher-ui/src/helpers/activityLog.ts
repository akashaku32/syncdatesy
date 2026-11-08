import { getUiState } from "@/helpers/log";
import {
  ConnectorActivityDetailResponse,
  ConnectorActivityLog,
  Status,
} from "@/types/connectors";

import { QueryClient } from "@tanstack/react-query";

interface ActivityCache {
  logs: ConnectorActivityLog[];
  last_updated?: string;
  _updateId?: number;
  [key: string]: unknown;
}

interface TerminalMigrationPatch {
  overallStatus: string;
  message?: string;
  status: Status;
}

const terminalMigrationPatches = new Map<string, TerminalMigrationPatch>();

const terminalPatchKey = (
  connectionId: number,
  migrationSessionId: number,
): string => `${connectionId}:${migrationSessionId}`;

const isTerminalLogStatus = (status?: string | null): boolean => {
  const normalized = (status || "").toUpperCase();
  return ["S", "F", "E", "P"].includes(normalized);
};

export const isTerminalOverallStatus = (status?: string | null): boolean => {
  const normalized = (status || "").trim().toLowerCase();
  return (
    normalized === "s" ||
    normalized === "e" ||
    normalized === "f" ||
    normalized === "p" ||
    normalized.includes("success") ||
    normalized.includes("completed") ||
    normalized.includes("failed") ||
    normalized.includes("error")
  );
};

export const isTransientMigrationMessage = (
  message?: string | null,
): boolean => {
  if (!message) return false;
  const lower = message.toLowerCase();
  if (
    lower.includes("migration in progress") ||
    lower.includes("update schema in progress")
  ) {
    return true;
  }
  return lower.includes("retrying");
};

export const isDetailedMigrationMessage = (
  message?: string | null,
): boolean => {
  if (!message) return false;
  if (isTransientMigrationMessage(message)) return false;
  const lower = message.toLowerCase();
  if (lower.includes("migration in progress")) return false;
  if (lower.includes("update schema")) return true;
  return (
    lower.includes(" seconds") ||
    lower.includes("total rows") ||
    lower.includes("tables processed") ||
    lower.includes("completed with warnings") ||
    (lower.includes("tables:") &&
      (lower.includes("completed") ||
        lower.includes("failed") ||
        lower.includes("successful"))) ||
    (lower.includes("completed") && lower.includes("tables:"))
  );
};

const resolveMigrationSessionId = (
  log: ConnectorActivityLog,
): number | null => {
  const sessionId = Number(log.migration_id ?? log.session_id);
  return Number.isNaN(sessionId) ? null : sessionId;
};

const isTerminalMigrationLog = (log: ConnectorActivityLog): boolean =>
  isTerminalLogStatus(log.status) && !isTransientMigrationMessage(log.message);

export const resolveCompletionMessage = (
  currentMessage: string,
  isFailed: boolean,
  candidateMessage?: string,
): string => {
  if (isUpdateSchemaMessage(candidateMessage)) {
    return candidateMessage as string;
  }
  if (isUpdateSchemaMessage(currentMessage)) {
    return currentMessage;
  }
  if (isDetailedMigrationMessage(candidateMessage)) {
    return candidateMessage as string;
  }
  if (isDetailedMigrationMessage(currentMessage)) {
    return currentMessage;
  }
  if (
    candidateMessage &&
    !isTransientMigrationMessage(candidateMessage) &&
    !candidateMessage.toLowerCase().includes("migration in progress") &&
    !candidateMessage.toLowerCase().includes("update schema in progress")
  ) {
    return candidateMessage;
  }
  return isFailed ? "Migration failed" : "Migration completed successfully";
};

const isGenericInProgressMessage = (message?: string | null): boolean => {
  if (!message) return false;
  const lower = message.toLowerCase();
  return (
    lower.includes("migration in progress") ||
    lower.includes("update schema in progress")
  );
};

export const resolveJobLevelBannerMessage = (
  overallStatus: string | undefined | null,
  jobLevelMessage: string | undefined | null,
  fallbackMessage?: string,
): string | null => {
  const overall = (overallStatus || "").toLowerCase();
  const isTerminal =
    overall.includes("completed") ||
    overall.includes("success") ||
    overall.includes("failed") ||
    overall.includes("error");

  if (jobLevelMessage && !isGenericInProgressMessage(jobLevelMessage)) {
    if (!isTerminal || !isTransientMigrationMessage(jobLevelMessage)) {
      return jobLevelMessage;
    }
  }

  if (!isTerminal) {
    return null;
  }

  const isFailed = overall.includes("failed") || overall.includes("error");
  return resolveCompletionMessage(
    jobLevelMessage || "",
    isFailed,
    fallbackMessage,
  );
};

const storeTerminalMigrationPatch = (
  connectionId: number,
  migrationSessionId: number,
  patch: TerminalMigrationPatch,
) => {
  terminalMigrationPatches.set(
    terminalPatchKey(connectionId, migrationSessionId),
    patch,
  );
};

export const clearTerminalMigrationPatch = (
  connectionId: number,
  migrationSessionId: number,
) => {
  terminalMigrationPatches.delete(
    terminalPatchKey(connectionId, migrationSessionId),
  );
};

export const applyTerminalMigrationPatches = (
  connectionId: number,
  logs: ConnectorActivityLog[],
): ConnectorActivityLog[] =>
  logs.map((log) => {
    const sessionId = resolveMigrationSessionId(log);
    if (sessionId === null) return log;

    const patch = terminalMigrationPatches.get(
      terminalPatchKey(connectionId, sessionId),
    );
    if (!patch) return log;

    if (isTerminalMigrationLog(log)) {
      return log;
    }

    const nextMessage = resolveCompletionMessage(
      log.message,
      patch.status === "E",
      patch.message,
    );

    return {
      ...log,
      status: patch.status,
      message: nextMessage,
      ui_state: getUiState(undefined, patch.status, nextMessage),
    };
  });

export const patchConnectorActivityLogs = (
  queryClient: QueryClient,
  connectionId: number,
  updater: (_logs: ConnectorActivityLog[]) => ConnectorActivityLog[],
) => {
  const queries = queryClient.getQueriesData({
    queryKey: ["connectorActivity", Number(connectionId)],
    exact: false,
  });

  queries.forEach(([queryKey]) => {
    queryClient.setQueryData(queryKey, (oldData: ActivityCache | undefined) => {
      if (!oldData?.logs) return oldData;
      const logs = updater(oldData.logs);
      return {
        ...oldData,
        logs,
        last_updated: new Date().toISOString(),
        _updateId: Math.random(),
      };
    });
  });

  queryClient.invalidateQueries({
    queryKey: ["connectorActivity", Number(connectionId)],
    exact: false,
    refetchType: "none",
  });
};

const isUpdateSchemaMessage = (message?: string | null): boolean =>
  Boolean(message && message.toLowerCase().includes("update schema"));

export const patchActivityLogForMigration = (
  queryClient: QueryClient,
  connectionId: number,
  migrationSessionId: number,
  {
    overallStatus,
    message,
  }: {
    overallStatus: string;
    message?: string;
  },
) => {
  const rawStatus = overallStatus.toLowerCase();
  const isFailed = rawStatus.includes("failed") || rawStatus.includes("error");
  const isCompleted =
    rawStatus.includes("success") || rawStatus.includes("completed");

  if (!isFailed && !isCompleted) return;

  const newStatus: Status = isFailed ? "E" : "S";

  storeTerminalMigrationPatch(connectionId, migrationSessionId, {
    overallStatus,
    message,
    status: newStatus,
  });

  patchConnectorActivityLogs(queryClient, connectionId, (logs) =>
    logs.map((log) => {
      const sessionId = resolveMigrationSessionId(log);
      const matchesSession =
        sessionId === migrationSessionId && sessionId !== null;
      const matchesLogId =
        log.log_id !== null &&
        log.log_id !== undefined &&
        migrationSessionId !== null &&
        migrationSessionId !== undefined &&
        Number(log.session_id) === migrationSessionId;
      if (!matchesSession && !matchesLogId) return log;

      if (isTerminalMigrationLog(log)) {
        return log;
      }

      const nextMessage = resolveCompletionMessage(
        log.message,
        isFailed,
        message,
      );

      return {
        ...log,
        status: newStatus,
        message: nextMessage,
        ui_state: getUiState(undefined, newStatus, nextMessage),
      };
    }),
  );
};

export const applyTerminalDetailsPatch = (
  connectionId: number,
  migrationSessionId: number,
  data: ConnectorActivityDetailResponse | undefined,
): ConnectorActivityDetailResponse | undefined => {
  if (!data) return data;

  const patch = terminalMigrationPatches.get(
    terminalPatchKey(connectionId, migrationSessionId),
  );
  if (!patch) return data;

  const apiOverall = (data.overall_status || "").trim().toLowerCase();
  const apiIsTerminal = isTerminalOverallStatus(apiOverall);
  const apiMessageIsStale = isTransientMigrationMessage(data.job_level_message);

  if (apiIsTerminal && !apiMessageIsStale) {
    return data;
  }

  const isFailed =
    patch.overallStatus.includes("failed") ||
    patch.overallStatus.includes("error") ||
    patch.status === "E";
  const nextMessage = resolveCompletionMessage(
    data.job_level_message || "",
    isFailed,
    patch.message,
  );

  return {
    ...data,
    overall_status: isFailed ? "failed" : "completed",
    job_level_message: nextMessage,
  };
};
