import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorSelectedTable } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

type FetchTableStatusResponse = {
  tables: ConnectorSelectedTable[];
  schema_refresh_in_progress?: boolean;
  readable_time_frequency?: string | null;
  next_sync_time?: string | null;
};

type GetTableStatusResponse = {
  table_statuses: Array<{
    table_name: string;
    status: string;
  }>;
  schema_refresh_in_progress?: boolean;
  readable_time_frequency?: string | null;
  next_sync_time?: string | null;
};

const fetchTableStatus = async (
  id: number,
): Promise<FetchTableStatusResponse> => {
  const { data } = await AxiosInstance.get<GetTableStatusResponse>(
    ServerRoutes.connector.getTableStatus(id),
  );

  const tables: ConnectorSelectedTable[] =
    data.table_statuses?.map((item, index) => ({
      tbl_id: index,
      table: item.table_name,
      sequence: index,
      status: (item.status as "in_progress" | "completed" | "failed") || null,
    })) || [];

  return {
    tables,
    schema_refresh_in_progress: data.schema_refresh_in_progress,
    readable_time_frequency: data.readable_time_frequency,
    next_sync_time: data.next_sync_time,
  };
};

export default function useFetchTableStatus(
  id: number,
  enabled: boolean = true,
) {
  const { data } = useQuery<FetchTableStatusResponse>({
    queryKey: ["TableStatus", id],
    queryFn: () => fetchTableStatus(id),
    enabled: !!id && enabled,
    refetchInterval: false,
  });

  return { data };
}
