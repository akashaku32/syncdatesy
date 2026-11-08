import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { type TableExportSetting } from "../TableExportSettingsModal";
import { useMutation, useQueryClient } from "@tanstack/react-query";

type UpdateTableExportSettingsPayload = {
  tableName: string;
  settings: TableExportSetting;
};

const updateTableExportSettings = (
  connectorId: number,
  payload: UpdateTableExportSettingsPayload,
) =>
  AxiosInstance.patch(
    ServerRoutes.connector.updateTableExportSettings(
      connectorId,
      payload.tableName,
    ),
    payload.settings,
  );

const useUpdateTableExportSettings = ({
  connectorId,
}: {
  connectorId: number;
}) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: UpdateTableExportSettingsPayload) =>
      updateTableExportSettings(connectorId, payload),

    onSuccess: async () => {
      queryClient.invalidateQueries({
        queryKey: ["ReverseSchema", connectorId],
      });
    },
  });
};

export default useUpdateTableExportSettings;
