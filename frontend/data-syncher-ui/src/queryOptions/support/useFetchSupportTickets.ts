import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type SupportTicketResponse } from "@/types/support";

import { useQuery } from "@tanstack/react-query";

const fetchSupportTickets = async () => {
  const { data } = await AxiosInstance.get<SupportTicketResponse[]>(
    ServerRoutes.supportTickets.list(),
  );
  return data;
};

const useFetchSupportTickets = (enabled = true) => {
  return useQuery({
    queryKey: ["support-tickets-list"],
    queryFn: fetchSupportTickets,
    enabled,
  });
};

export default useFetchSupportTickets;
