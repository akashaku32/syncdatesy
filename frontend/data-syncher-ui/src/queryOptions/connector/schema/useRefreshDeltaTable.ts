import { toaster } from "@/components/ui/toaster";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface RefreshDeltaTablePayload {
  connection_id: number;
  table_name: string;
}

const refreshDeltaTable = (payload: RefreshDeltaTablePayload) =>
  AxiosInstance.post(ServerRoutes.connector.refreshDeltaTable(), payload);

export default function useRefreshDeltaTable({
  connectionId,
}: {
  connectionId: number;
}) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["refreshDeltaTable", connectionId],
    mutationFn: refreshDeltaTable,
    onSuccess: (response, variables) => {
      toaster.success({ title: response.data.message });

      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", variables.connection_id],
      });
      queryClient.invalidateQueries({
        queryKey: ["TableStatus", variables.connection_id],
      });
    },
  });
}
