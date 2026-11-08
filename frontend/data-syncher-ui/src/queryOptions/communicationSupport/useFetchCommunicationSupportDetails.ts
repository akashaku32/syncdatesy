import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type CommunicationSupport } from "@/types/communicationSupport";

import { useQuery } from "@tanstack/react-query";

const fetchCommunicationSupportDetails = async () => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.communicationSupport.getDetails(),
  );
  return data.data;
};

export default function useFetchCommunicationSupportDetails() {
  return useQuery<CommunicationSupport>({
    queryKey: ["CommunicationSupportDetails"],
    queryFn: fetchCommunicationSupportDetails,
  });
}
