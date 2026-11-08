import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import {
  type CreateSupportTicketPayload,
  type CreateSupportTicketResponse,
} from "@/types/support";

import { useMutation } from "@tanstack/react-query";

const createSupportTicket = async (
  payload: CreateSupportTicketPayload,
): Promise<CreateSupportTicketResponse> => {
  const formData = new FormData();

  formData.append("connection", payload.connection);
  formData.append("source_type", payload.source_type);
  formData.append("connection_name", payload.connection_name);
  formData.append("subject", payload.subject);
  formData.append("description", payload.description);
  formData.append("category", payload.category);
  formData.append("issue_type", payload.issue_type);

  if (payload.attachment) {
    formData.append("attachment", payload.attachment);
  }

  if (payload.attachments && payload.attachments.length > 0) {
    payload.attachments.forEach((file) => {
      formData.append("attachments", file);
    });
  }

  const { data } = await AxiosInstance.post<CreateSupportTicketResponse>(
    ServerRoutes.supportTickets.create(),
    formData,
    {
      headers: {
        "Content-Type": "multipart/form-data",
      },
    },
  );

  return data;
};

export default function useCreateSupportTicket() {
  return useMutation<
    CreateSupportTicketResponse,
    Error,
    CreateSupportTicketPayload
  >({
    mutationFn: createSupportTicket,
  });
}
