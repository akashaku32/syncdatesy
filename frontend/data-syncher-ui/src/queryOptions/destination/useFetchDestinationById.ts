import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type Destination } from "@/types/destination";

import { useQuery } from "@tanstack/react-query";

const fetchDestinationById = async (id: string): Promise<Destination> => {
  const { data } = await AxiosInstance.get<Destination>(
    ServerRoutes.destination.fetchDestinationById(id),
  );
  return data;
};

export const useFetchDestinationById = (id: string) => {
  return useQuery({
    queryKey: ["destination", id],
    queryFn: () => fetchDestinationById(id),
    enabled: !!id,
  });
};
