import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type MasterDestinationList } from "@/types/destination";

import { useQuery } from "@tanstack/react-query";

const fetchMasterDestinationList = async () => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.destination.listMasterDestinations(),
  );
  return data;
};

export default function useFetchMasterDestinationList() {
  return useQuery<MasterDestinationList>({
    queryKey: ["masterDestinationList"],
    queryFn: fetchMasterDestinationList,
  });
}
