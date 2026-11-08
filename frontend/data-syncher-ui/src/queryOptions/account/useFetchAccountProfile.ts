import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type AccountProfile } from "@/types/accountProfile";

import { useQuery } from "@tanstack/react-query";

const fetchAccountProfile = async () => {
  const { data } = await AxiosInstance.get(ServerRoutes.account.profile());
  return data?.data ?? data;
};

export default function useFetchAccountProfile() {
  return useQuery<AccountProfile>({
    queryKey: ["AccountProfile"],
    queryFn: fetchAccountProfile,
  });
}
