import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorSyncStats } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

const fetchConnectorUsageById = async (id: number) => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.connector.fetchConnectorUsage(id),
  );
  return data;
};

export default function useFetchConnectorUsageById(id: number) {
  return useQuery<ConnectorSyncStats>({
    queryKey: ["ConnectorUsage", id],
    queryFn: () => fetchConnectorUsageById(id),
    enabled: !!id,
  });
}
