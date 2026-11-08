import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const useCloseTicket = (ticketId: number | string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      AxiosInstance.patch(ServerRoutes.supportTickets.close(ticketId), {
        status: "Closed",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["support-ticket-detail", ticketId],
      });
    },
  });
};

export default useCloseTicket;
