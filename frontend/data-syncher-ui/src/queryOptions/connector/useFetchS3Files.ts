import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { fileSourceApiSegment } from "./fileSourceUtils";
import {
  type S3FileItem,
  type S3ListFilesRequest,
  type S3ListFilesResponse,
  type SFTPListFilesRequest,
} from "./types/connector";
import { useQuery } from "@tanstack/react-query";

export type {
  S3ListFilesRequest,
  SFTPListFilesRequest,
  S3ListFilesResponse,
  S3FileItem,
};

const fetchS3Files = async (
  data: S3ListFilesRequest | SFTPListFilesRequest,
) => {
  const isSftp =
    !!(data as SFTPListFilesRequest).sftp_host ||
    !!(data as SFTPListFilesRequest).root_folder ||
    !!data.isSftp;
  const source = fileSourceApiSegment(
    data.sourceType || (isSftp ? "sftp" : "s3"),
  );
  const endpoint = ServerRoutes.connector.listFiles({ source });

  const { data: responseData } = await AxiosInstance.post(endpoint, data);

  let tables: S3FileItem[] = [];
  if (Array.isArray(responseData)) {
    tables = responseData;
  } else if (responseData?.result) {
    if (Array.isArray(responseData.result)) {
      tables = responseData.result;
    } else if (
      Array.isArray((responseData.result as Record<string, unknown>).tables)
    ) {
      tables = (responseData.result as Record<string, unknown>)
        .tables as S3FileItem[];
    } else if (
      Array.isArray((responseData.result as Record<string, unknown>).files)
    ) {
      tables = (responseData.result as Record<string, unknown>)
        .files as S3FileItem[];
    }
  } else if (Array.isArray(responseData?.tables)) {
    tables = responseData.tables;
  } else if (Array.isArray(responseData?.files)) {
    tables = responseData.files;
  } else if (Array.isArray(responseData?.data)) {
    tables = responseData.data;
  } else if (Array.isArray(responseData?.data?.tables)) {
    tables = responseData.data.tables;
  }

  const resObj =
    responseData && typeof responseData === "object" ? responseData : {};
  const resResult = (resObj as Record<string, unknown>).result as
    | Record<string, unknown>
    | undefined;

  return {
    ...resObj,
    tables,
    total_count:
      (resObj as Record<string, unknown>).total_count ??
      resResult?.total_count ??
      tables.length,
  } as S3ListFilesResponse;
};

export default function useFetchS3Files(
  data: S3ListFilesRequest | SFTPListFilesRequest,
  enabled: boolean = true,
) {
  const s3Bucket = (data as S3ListFilesRequest).s3_bucket;
  const sftpHost = (data as SFTPListFilesRequest).sftp_host;
  const rootFolder = (data as SFTPListFilesRequest).root_folder;
  const sourceType = data.sourceType;
  const accountName = (data as Record<string, unknown>).account_name as
    | string
    | undefined;
  const containerName = (data as Record<string, unknown>).container_name as
    | string
    | undefined;

  return useQuery<S3ListFilesResponse>({
    queryKey: ["S3Files", data],
    queryFn: () => fetchS3Files(data),
    enabled:
      enabled &&
      (!!s3Bucket ||
        !!sftpHost ||
        !!rootFolder ||
        !!accountName ||
        !!containerName ||
        !!sourceType ||
        !!data.connection_id),
  });
}
