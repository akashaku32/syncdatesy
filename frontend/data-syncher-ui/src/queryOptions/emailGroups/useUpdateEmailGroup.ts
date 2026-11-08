import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type EmailGroup, type EmailGroupUpdate } from "@/types/emailGroups";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface UpdatePayload {
  id: number | string;
  payload: EmailGroupUpdate;
}

const updateEmailGroup = async ({
  id,
  payload,
}: UpdatePayload): Promise<EmailGroup> => {
  const { data } = await AxiosInstance.put(
    ServerRoutes.emailGroups.update(id),
    payload,
  );
  return data.data || data;
};

export default function useUpdateEmailGroup() {
  const queryClient = useQueryClient();
  return useMutation<EmailGroup, Error, UpdatePayload>({
    mutationFn: updateEmailGroup,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["EmailGroups"],
      });
    },
  });
}
