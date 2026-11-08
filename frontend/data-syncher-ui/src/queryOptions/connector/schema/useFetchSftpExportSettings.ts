import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useQuery } from "@tanstack/react-query";

type SftpExportSettingsResponse = {
  target_folder?: string;
  file_format?: "csv" | "json" | "parquet" | string;
  csv_delimiter?: string;
  csv_quote_char?: string;
};

const fetchSftpExportSettings = async (
  connectorId: number,
): Promise<SftpExportSettingsResponse> => {
  const { data } = await AxiosInstance.get<SftpExportSettingsResponse>(
    ServerRoutes.connector.updateSftpExportSettings(connectorId),
  );
  return data;
};

const useFetchSftpExportSettings = ({
  connectorId,
}: {
  connectorId: number;
}) => {
  return useQuery<SftpExportSettingsResponse>({
    queryKey: ["sftpExportSettings", connectorId],
    queryFn: () => fetchSftpExportSettings(connectorId),
    enabled: !!connectorId,
    staleTime: 30_000,
  });
};

export default useFetchSftpExportSettings;
export type { SftpExportSettingsResponse };
