import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type SupportTicketReply } from "@/types/support";

import { useQuery } from "@tanstack/react-query";

const useFetchTicketReplies = (ticketId: number | string, enabled = true) => {
  return useQuery({
    queryKey: ["support-ticket-replies", ticketId],
    queryFn: async () => {
      const { data } = await AxiosInstance.get<SupportTicketReply[]>(
        ServerRoutes.supportTickets.replies(ticketId),
      );
      return data;
    },
    enabled: !!ticketId && enabled,
  });
};

export default useFetchTicketReplies;
