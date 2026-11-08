import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorConfigResponse } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

const fetchConnectorConfig = async ({
  type,
  id,
}: {
  type: string;
  id: number;
}): Promise<ConnectorConfigResponse> => {
  const { data } = await AxiosInstance.get<ConnectorConfigResponse>(
    ServerRoutes.connector.fetchConnectorConfig({ type, id }),
  );
  return data;
};

const useFetchConnectorConfig = ({
  type,
  id,
}: {
  type: string;
  id: number;
}) => {
  return useQuery({
    queryKey: ["connectorConfig", id],
    queryFn: () => fetchConnectorConfig({ type, id }),
    enabled: !!id && !!type,
    staleTime: 30_000,
  });
};

export default useFetchConnectorConfig;
