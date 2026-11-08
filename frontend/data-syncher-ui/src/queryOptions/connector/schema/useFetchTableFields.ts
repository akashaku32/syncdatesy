import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorTable } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

interface FetchTableFieldsResponse {
  table_fields: ConnectorTable["table_fields"];
  primary_keys: string[];
  row_filter_config?: ConnectorTable["row_filter_config"];
  row_filter?: ConnectorTable["row_filter"];
}

const fetchTableFields = async (
  connectionId: number,
  tableName: string,
): Promise<FetchTableFieldsResponse> => {
  const { data } = await AxiosInstance.get<FetchTableFieldsResponse>(
    ServerRoutes.connector.fetchTableFields(connectionId, tableName),
  );
  return data;
};

export default function useFetchTableFields(
  connectionId: number,
  tableName: string,
  enabled: boolean,
) {
  return useQuery<FetchTableFieldsResponse>({
    queryKey: ["tableFields", connectionId, tableName],
    queryFn: () => fetchTableFields(connectionId, tableName),
    enabled,
    retry: 1,
  });
}
