import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation } from "@tanstack/react-query";

type UpdateSftpExportSettingsPayload = {
  target_folder: string;
  file_format: "csv" | "json" | "parquet";
  csv_delimiter?: string;
  csv_quote_char?: string;
};

const updateSftpExportSettings = (
  connectorId: number,
  payload: UpdateSftpExportSettingsPayload,
) =>
  AxiosInstance.put(
    ServerRoutes.connector.updateSftpExportSettings(connectorId),
    payload,
  );

const useUpdateSftpExportSettings = ({
  connectorId,
}: {
  connectorId: number;
}) => {
  return useMutation({
    mutationFn: (payload: UpdateSftpExportSettingsPayload) =>
      updateSftpExportSettings(connectorId, payload),
  });
};

export default useUpdateSftpExportSettings;
export type { UpdateSftpExportSettingsPayload };
