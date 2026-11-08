import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const useSendTicketReply = (ticketId: number | string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      message,
      attachments,
    }: {
      message: string;
      attachments?: File[];
    }) => {
      const formData = new FormData();
      formData.append("message", message);
      if (attachments && attachments.length > 0) {
        attachments.forEach((file) => {
          formData.append("attachments", file);
        });
      }
      return AxiosInstance.post(
        ServerRoutes.supportTickets.replies(ticketId),
        formData,
        {
          headers: {
            "Content-Type": "multipart/form-data",
          },
        },
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["support-ticket-replies", ticketId],
      });
    },
  });
};

export default useSendTicketReply;
