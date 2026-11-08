import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type Connector } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

const fetchConnectorById = async (id: number): Promise<Connector> => {
  const { data } = await AxiosInstance.get<Connector>(
    ServerRoutes.connector.fetchConnectorById(id),
  );
  return data;
};

export const useFetchConnectorById = (id: number) => {
  return useQuery({
    queryKey: ["connector", id],
    queryFn: () => fetchConnectorById(id),
    enabled: !!id,
  });
};
