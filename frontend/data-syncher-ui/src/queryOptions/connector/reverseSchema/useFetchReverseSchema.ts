import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorTable } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

export type ReverseSchemaResponse = {
  source_tables?: ConnectorTable[];
  destination_tables?: ConnectorTable[];
  tables?: ConnectorTable[];
};

const fetchReverseSchema = async (
  id: number,
): Promise<ReverseSchemaResponse> => {
  const { data } = await AxiosInstance.get<ReverseSchemaResponse>(
    ServerRoutes.connector.fetchReverseSchema(id),
  );
  return data;
};

export default function useFetchReverseSchema(id: number) {
  return useQuery<ReverseSchemaResponse>({
    queryKey: ["ReverseSchema", id],
    queryFn: () => fetchReverseSchema(id),
    enabled: !!id,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    refetchOnMount: true,
  });
}

export { fetchReverseSchema };
