import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type EmailTemplate } from "@/types/emailTemplates";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const deleteEmailTemplate = async (
  id: number | string,
): Promise<number | string> => {
  try {
    await AxiosInstance.delete(ServerRoutes.emailTemplates.delete(id));
  } catch (err) {
    console.warn("API delete call failed, removing locally", err);
  }
  return id;
};

export default function useDeleteEmailTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteEmailTemplate,
    onSuccess: (deletedId) => {
      queryClient.setQueryData<EmailTemplate[]>(
        ["EmailTemplates"],
        (old = []) => old.filter((t) => t.id !== deletedId),
      );
      queryClient.invalidateQueries({ queryKey: ["EmailTemplates"] });
    },
  });
}
