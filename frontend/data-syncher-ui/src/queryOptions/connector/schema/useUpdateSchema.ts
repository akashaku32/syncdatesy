import { toaster } from "@/components/ui/toaster";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { queryClient } from "@/lib/react-query-client";

import { useMutation } from "@tanstack/react-query";

const updateSchema = (connectorId: number) =>
  AxiosInstance.post(ServerRoutes.connector.updateSchema(connectorId));

const triggerUpdateSchemaStatus = (connectorId: number) =>
  AxiosInstance.get(ServerRoutes.connector.updateSchemaStatus(connectorId));

const useUpdateSchema = ({ connectorId }: { connectorId: number }) => {
  return useMutation({
    mutationKey: ["updateSchema", connectorId],
    mutationFn: () => updateSchema(connectorId),
    onSuccess: (response) => {
      toaster.success({ title: response.data.message });
      queryClient.setQueryData(["SchemaStatus", connectorId], {
        is_in_progress: true,
        current_job: "Updating schema...",
      });
      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", connectorId],
      });
      queryClient.refetchQueries({
        queryKey: ["ConnectorTable", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["SchemaStatus", connectorId],
      });

      triggerUpdateSchemaStatus(connectorId);
    },
  });
};

export default useUpdateSchema;
