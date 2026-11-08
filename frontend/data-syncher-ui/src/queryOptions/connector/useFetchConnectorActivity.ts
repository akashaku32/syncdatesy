import ServerRoutes from "@/constants/server-routes";
import { applyTerminalMigrationPatches } from "@/helpers/activityLog";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorActivityResponse } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

const fetchConnectorActivity = async (
  id: number,
  filterDays: number,
  status?: string,
): Promise<ConnectorActivityResponse> => {
  const { data } = await AxiosInstance.get<ConnectorActivityResponse>(
    ServerRoutes.connector.fetchConnectionActivity({ id, filterDays, status }),
  );
  return data;
};

const useFetchConnectorActivity = (
  id: number,
  filterDays: number,
  status?: string,
) => {
  return useQuery({
    queryKey: ["connectorActivity", id, filterDays, status],
    queryFn: () => fetchConnectorActivity(id, filterDays, status),
    enabled: !!id && filterDays > 0,
    select: (data) => ({
      ...data,
      logs: applyTerminalMigrationPatches(id, data.logs ?? []),
    }),
  });
};

export default useFetchConnectorActivity;
