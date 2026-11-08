import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type SupportTicketResponse } from "@/types/support";

import { useQuery } from "@tanstack/react-query";

const fetchSupportTicketDetail = async (id: number | string) => {
  const { data } = await AxiosInstance.get<
    SupportTicketResponse | SupportTicketResponse[]
  >(ServerRoutes.supportTickets.detail(id));
  return Array.isArray(data) ? data[0] : data;
};

const useFetchSupportTicketDetail = (id: number | string, enabled = true) => {
  return useQuery({
    queryKey: ["support-ticket-detail", id],
    queryFn: () => fetchSupportTicketDetail(id),
    enabled: !!id && enabled,
  });
};

export default useFetchSupportTicketDetail;
