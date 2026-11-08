import { toaster } from "@/components/ui/toaster";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface DeleteDeltaTablePayload {
  connection_id: number;
  table_name: string;
}

const deleteDeltaTable = (payload: DeleteDeltaTablePayload) =>
  AxiosInstance.post(ServerRoutes.connector.deleteDeltaTable(), payload);

export default function useDeleteDeltaTable({
  connectionId,
}: {
  connectionId: number;
}) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["deleteDeltaTable", connectionId],
    mutationFn: deleteDeltaTable,
    onSuccess: (response, variables) => {
      toaster.success({
        title: response.data?.message || "Delta deleted successfully",
      });

      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", variables.connection_id],
      });
      queryClient.invalidateQueries({
        queryKey: ["TableStatus", variables.connection_id],
      });
    },
  });
}
