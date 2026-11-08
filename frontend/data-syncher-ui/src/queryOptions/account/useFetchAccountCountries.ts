import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useQuery } from "@tanstack/react-query";

const fetchAccountCountries = async () => {
  const { data } = await AxiosInstance.get(ServerRoutes.account.countries());
  return data?.data ?? data;
};

export default function useFetchAccountCountries() {
  return useQuery({
    queryKey: ["AccountCountries"],
    queryFn: fetchAccountCountries,
  });
}
