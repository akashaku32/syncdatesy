import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useQuery } from "@tanstack/react-query";

export interface ExportConfigResponse {
  destination: {
    name: string;
    is_file_based: boolean;
    supports_notification_groups: boolean;
    path_label: string | null;
    supported_formats: string[];
    default_format: string;
    csv_defaults?: {
      delimiter?: string;
      quote_char?: string;
    };
  };
}

const fetchExportConfig = async (
  destinationName: string,
): Promise<ExportConfigResponse> => {
  const { data } = await AxiosInstance.get<ExportConfigResponse>(
    ServerRoutes.destination.fetchExportConfig(destinationName),
  );
  return data;
};

export const useFetchExportConfig = (destinationName: string) => {
  const normalizedName = destinationName?.toLowerCase() || "";
  return useQuery({
    queryKey: ["destinationExportConfig", normalizedName],
    queryFn: () => fetchExportConfig(normalizedName),
    enabled: !!normalizedName,
    staleTime: 5 * 60 * 1000, // Cache config for 5 minutes
  });
};
