import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type SchemaStatusResponse } from "@/types/connectors";

import { useQuery } from "@tanstack/react-query";

const checkSchemaStatus = async (
  connectionId: number,
): Promise<SchemaStatusResponse> => {
  const { data } = await AxiosInstance.get<SchemaStatusResponse>(
    ServerRoutes.connector.updateSchemaStatus(connectionId),
  );
  return data;
};

const useUpdateSchemaStatus = (connectionId: number, enabled: boolean) => {
  const query = useQuery({
    queryKey: ["SchemaStatus", connectionId],
    queryFn: () => checkSchemaStatus(connectionId),
    enabled: !!connectionId && enabled,
    refetchInterval: false,
    staleTime: 0,
    refetchOnMount: true,
  });

  return { ...query, status: query.data };
};

export default useUpdateSchemaStatus;
