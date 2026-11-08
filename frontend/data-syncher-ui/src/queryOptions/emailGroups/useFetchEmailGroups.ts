import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type EmailGroup } from "@/types/emailGroups";

import { useQuery } from "@tanstack/react-query";

const fetchEmailGroups = async (): Promise<EmailGroup[]> => {
  const { data } = await AxiosInstance.get(ServerRoutes.emailGroups.list());
  return Array.isArray(data) ? data : data.data || [];
};

export default function useFetchEmailGroups() {
  return useQuery<EmailGroup[]>({
    queryKey: ["EmailGroups"],
    queryFn: fetchEmailGroups,
  });
}
