import { toaster } from "@/components/ui/toaster";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { queryClient } from "@/lib/react-query-client";

import { useMutation } from "@tanstack/react-query";

const refreshSchema = (connectorId: number) =>
  AxiosInstance.post(ServerRoutes.connector.refreshSchema(connectorId));

const useRefreshSchema = ({ connectorId }: { connectorId: number }) => {
  return useMutation({
    mutationKey: ["refreshSchema", connectorId],
    mutationFn: () => refreshSchema(connectorId),
    onSuccess: (response) => {
      toaster.success({ title: response.data.message });
      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["TableStatus", connectorId],
      });
    },
  });
};

export default useRefreshSchema;
