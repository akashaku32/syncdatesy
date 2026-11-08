import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type CreateConnectionPayload } from "@/types/connectors";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface UpdateConnectionResponse {
  message: string;
  auth_url?: string;
  requires_primary_key_selection?: boolean;
  pending_primary_key_tables?: string[];
  connection_id?: number;
  source_config_id?: number;
}

const updateConnectorConfig = async (
  connectorId: number,
  type: string,
  payload: CreateConnectionPayload,
): Promise<UpdateConnectionResponse> => {
  const { data } = await AxiosInstance.post(
    ServerRoutes.connector.updateConnectorConfig({ connectorId, type }),
    payload,
  );
  return data;
};

const useUpdateConnectorConfig = ({
  connectorId,
  type,
}: {
  connectorId: number;
  type: string;
}) => {
  const queryClient = useQueryClient();
  return useMutation<
    UpdateConnectionResponse,
    unknown,
    CreateConnectionPayload
  >({
    mutationFn: (payload) => updateConnectorConfig(connectorId, type, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["connectorConfig", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["SelectedTables", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["tableFields", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["SchemaStatus", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["TableStatus", connectorId],
      });
      queryClient.invalidateQueries({
        queryKey: ["ReverseSchema", connectorId],
      });
    },
  });
};

export default useUpdateConnectorConfig;
