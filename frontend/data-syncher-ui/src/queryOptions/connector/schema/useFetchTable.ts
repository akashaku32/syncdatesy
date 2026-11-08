import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorTablesResponse } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

const fetchConnectorTableById = async (id: number) => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.connector.fetchConnectorTable(id),
  );
  return data as ConnectorTablesResponse;
};

export default function useFetchConnectorTableById(id: number) {
  return useQuery<ConnectorTablesResponse>({
    queryKey: ["ConnectorTable", id],
    queryFn: () => fetchConnectorTableById(id),
    enabled: !!id,
  });
}
