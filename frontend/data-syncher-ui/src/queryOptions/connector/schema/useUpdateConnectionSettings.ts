import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type ConnectorSettingsApiResponse } from "@/types/connectors";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const updateConnectionSettings = (
  connectorId: number,
  payload: Partial<ConnectorSettingsApiResponse>,
) =>
  AxiosInstance.patch(
    ServerRoutes.connector.fetchConnectorById(connectorId),
    payload,
  );

const useUpdateConnectionSettings = ({
  connectorId,
}: {
  connectorId: number;
}) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Partial<ConnectorSettingsApiResponse>) =>
      updateConnectionSettings(connectorId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["connector", connectorId],
      });
    },
  });
};

export default useUpdateConnectionSettings;
