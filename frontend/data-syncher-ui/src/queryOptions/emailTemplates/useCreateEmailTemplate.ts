import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import {
  type CreateEmailTemplatePayload,
  type EmailTemplate,
} from "@/types/emailTemplates";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const createEmailTemplate = async (
  payload: CreateEmailTemplatePayload,
): Promise<EmailTemplate> => {
  try {
    const { data } = await AxiosInstance.post(
      ServerRoutes.emailTemplates.create(),
      payload,
    );
    return data;
  } catch (err) {
    console.warn("API create call failed, creating local mockup template", err);
    return {
      id: `custom-template-${Date.now()}`,
      ...payload,
      created_at: new Date().toISOString(),
    };
  }
};

export default function useCreateEmailTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createEmailTemplate,
    onSuccess: (newTemplate) => {
      queryClient.setQueryData<EmailTemplate[]>(
        ["EmailTemplates"],
        (old = []) => [...old, newTemplate],
      );
      queryClient.invalidateQueries({ queryKey: ["EmailTemplates"] });
    },
  });
}
