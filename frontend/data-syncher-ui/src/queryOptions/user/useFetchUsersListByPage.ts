import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type PaginationResponse } from "@/types/pagination";
import { type UserTableItem } from "@/types/user";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

export interface FetchUsersParams {
  page: number;
  size: number;
  searchTerm?: string;
}

async function fetchUsersListByPage(
  params: FetchUsersParams,
): Promise<PaginationResponse<UserTableItem>> {
  const { page, size, searchTerm } = params;
  const { data } = await AxiosInstance.get(
    ServerRoutes.user.listUsersByPage({ page, size, searchTerm }),
  );
  return data;
}

export function useFetchUsersListByPage(params: FetchUsersParams) {
  return useQuery<PaginationResponse<UserTableItem>, Error>({
    queryKey: ["users", params.page, params.size, params.searchTerm],
    queryFn: () => fetchUsersListByPage(params),
    placeholderData: keepPreviousData,
  });
}
