import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { fileSourceApiSegment } from "./fileSourceUtils";
import {
  type ColumnSuggestion,
  type SuggestPrimaryKeysRequest,
  type SuggestPrimaryKeysResponse,
  type TableSuggestion,
} from "./types/connector";
import { useQuery } from "@tanstack/react-query";

export type {
  SuggestPrimaryKeysRequest,
  SuggestPrimaryKeysResponse,
  TableSuggestion,
  ColumnSuggestion,
};

const fetchSuggestPrimaryKeys = async (data: SuggestPrimaryKeysRequest) => {
  const isSftp = !!data.sftp_host || !!data.root_folder || !!data.isSftp;
  const source = fileSourceApiSegment(
    data.sourceType || (isSftp ? "sftp" : "s3"),
  );
  const endpoint = ServerRoutes.connector.suggestPrimaryKeys({ source });

  const { data: responseData } = await AxiosInstance.post(endpoint, data);
  const resObj = (responseData?.result ||
    responseData?.data ||
    responseData) as Record<string, unknown>;
  const tables = (resObj?.tables ||
    responseData?.tables ||
    []) as TableSuggestion[];

  return {
    ...responseData,
    ...resObj,
    tables,
  } as SuggestPrimaryKeysResponse;
};

export default function useSuggestPrimaryKeys(
  data: SuggestPrimaryKeysRequest,
  enabled: boolean = true,
) {
  return useQuery<SuggestPrimaryKeysResponse>({
    queryKey: ["SuggestPrimaryKeys", data],
    queryFn: () => fetchSuggestPrimaryKeys(data),
    enabled:
      enabled &&
      (!!data.connection_id ||
        !!data.s3_bucket ||
        !!data.sftp_host ||
        !!data.root_folder ||
        !!data.account_name ||
        !!data.container_name ||
        !!data.sourceType ||
        !!data.isSftp),
  });
}
