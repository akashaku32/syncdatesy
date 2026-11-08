import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type EmailGroup, type EmailGroupCreate } from "@/types/emailGroups";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const createEmailGroup = async (
  payload: EmailGroupCreate,
): Promise<EmailGroup> => {
  const { data } = await AxiosInstance.post(
    ServerRoutes.emailGroups.create(),
    payload,
  );
  return data.data || data;
};

export default function useCreateEmailGroup() {
  const queryClient = useQueryClient();
  return useMutation<EmailGroup, Error, EmailGroupCreate>({
    mutationFn: createEmailGroup,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["EmailGroups"],
      });
    },
  });
}
