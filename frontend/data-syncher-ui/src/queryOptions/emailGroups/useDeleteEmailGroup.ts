import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const deleteEmailGroup = async (id: number | string): Promise<void> => {
  await AxiosInstance.delete(ServerRoutes.emailGroups.delete(id));
};

export default function useDeleteEmailGroup() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, number | string>({
    mutationFn: deleteEmailGroup,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["EmailGroups"],
      });
    },
  });
}
