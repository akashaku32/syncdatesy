import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type DestinationTableItem } from "@/types/destination";

import { useQuery } from "@tanstack/react-query";

const fetchAllUserCreatedDestinationList = async () => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.destination.fetchAllUserCreatedDestinationList(),
  );
  return data.content;
};

export default function useFetchAllUserCreatedDestinationList() {
  return useQuery<DestinationTableItem[]>({
    queryKey: ["allUserCreatedDestinationList"],
    queryFn: fetchAllUserCreatedDestinationList,
  });
}
