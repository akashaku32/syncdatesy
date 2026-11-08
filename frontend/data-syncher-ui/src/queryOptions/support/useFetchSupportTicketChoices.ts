import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type SupportTicketChoicesResponse } from "@/types/support";

import { useQuery } from "@tanstack/react-query";

const fetchSupportTicketChoices = async () => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.supportTickets.choices(),
  );
  return data as SupportTicketChoicesResponse;
};

export default function useFetchSupportTicketChoices(enabled = true) {
  return useQuery<SupportTicketChoicesResponse>({
    queryKey: ["SupportTicketChoices"],
    queryFn: fetchSupportTicketChoices,
    enabled,
  });
}
