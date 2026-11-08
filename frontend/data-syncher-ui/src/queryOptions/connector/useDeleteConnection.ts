import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import {
  pipelineConnectionsQueryKey,
  pipelinesQueryKey,
} from "@/queryOptions/pipeline/usePipeline";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const deleteConnection = (connectorId: number) =>
  AxiosInstance.delete(ServerRoutes.connector.deleteConnection(connectorId));

const useDeleteConnection = ({ connectorId }: { connectorId: number }) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => deleteConnection(connectorId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["connector", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: pipelineConnectionsQueryKey,
      });
      queryClient.invalidateQueries({
        queryKey: pipelinesQueryKey,
      });
      queryClient.invalidateQueries({
        queryKey: ["connectors"],
      });
    },
  });
};

export default useDeleteConnection;
