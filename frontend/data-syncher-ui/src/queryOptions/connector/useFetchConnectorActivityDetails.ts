import ServerRoutes from "@/constants/server-routes";
import { applyTerminalDetailsPatch } from "@/helpers/activityLog";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorActivityDetailResponse } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

const fetchConnectorActivityDetails = async (
  migrationId: number,
  connectionId: number,
): Promise<ConnectorActivityDetailResponse> => {
  const { data } = await AxiosInstance.get<ConnectorActivityDetailResponse>(
    ServerRoutes.connector.fetchMigrationStatus({
      migrationId,
      connectionId,
    }),
  );
  return data;
};

const fetchLogDetails = async (
  connectionId: number,
  logId: number,
): Promise<ConnectorActivityDetailResponse> => {
  const { data } = await AxiosInstance.get<ConnectorActivityDetailResponse>(
    ServerRoutes.connector.fetchLogDetails({ connectionId, logId }),
  );
  return data;
};

const useFetchConnectorActivityDetails = ({
  migrationId,
  connectionId,
  logId,
}: {
  migrationId?: number;
  connectionId?: number;
  logId?: number;
}) => {
  return useQuery({
    queryKey: ["connectorActivityDetails", migrationId, connectionId, logId],
    queryFn: () => {
      if (migrationId && connectionId) {
        return fetchConnectorActivityDetails(migrationId, connectionId);
      }
      if (connectionId && logId) {
        return fetchLogDetails(connectionId, logId);
      }
      return Promise.reject(new Error("Missing required parameters"));
    },
    enabled: (!!migrationId && !!connectionId) || (!!connectionId && !!logId),
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
    select: (data) => {
      if (!migrationId || !connectionId) return data;
      return applyTerminalDetailsPatch(connectionId, migrationId, data) ?? data;
    },
    refetchInterval: (query) => {
      if (!migrationId) return false;

      const status = (query.state.data?.overall_status || "")
        .trim()
        .toLowerCase();
      const isTerminal =
        status === "s" ||
        status === "e" ||
        status === "f" ||
        status.includes("success") ||
        status.includes("completed") ||
        status.includes("failed") ||
        status.includes("error");

      return isTerminal ? false : 4000;
    },
  });
};

export default useFetchConnectorActivityDetails;
