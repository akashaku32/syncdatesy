import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type EmailTemplate } from "@/types/emailTemplates";

import { useQuery } from "@tanstack/react-query";

const fetchEmailTemplates = async (): Promise<EmailTemplate[]> => {
  try {
    const { data } = await AxiosInstance.get(
      ServerRoutes.emailTemplates.list(),
    );
    const templates = Array.isArray(data) ? data : data?.data || [];
    return templates;
  } catch (err) {
    console.warn("API fetch email templates error:", err);
    return [];
  }
};

export default function useFetchEmailTemplates() {
  return useQuery<EmailTemplate[]>({
    queryKey: ["EmailTemplates"],
    queryFn: fetchEmailTemplates,
  });
}
