import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorTable } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

interface FetchDestinationTableFieldsResponse {
  table_fields: ConnectorTable["table_fields"];
  primary_keys: string[];
}

const fetchDestinationTableFields = async (
  connectionId: number,
  tableName: string,
): Promise<FetchDestinationTableFieldsResponse> => {
  const { data } = await AxiosInstance.get<FetchDestinationTableFieldsResponse>(
    ServerRoutes.connector.fetchDestinationTableFields(connectionId, tableName),
  );
  return data;
};

export default function useFetchDestinationTableFields(
  connectionId: number,
  tableName: string,
  enabled: boolean,
) {
  return useQuery<FetchDestinationTableFieldsResponse>({
    queryKey: ["destinationTableFields", connectionId, tableName],
    queryFn: () => fetchDestinationTableFields(connectionId, tableName),
    enabled,
    retry: 1,
  });
}
