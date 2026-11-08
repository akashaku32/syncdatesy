import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type UpdateUserPayload } from "@/types/user";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const updateUser = async (
  id: number,
  payload: UpdateUserPayload,
): Promise<UpdateUserPayload> => {
  const { data } = await AxiosInstance.put(
    ServerRoutes.user.updateUser(id),
    payload,
  );
  return data;
};

export function useUpdateUser({ id }: { id: number }) {
  const queryClient = useQueryClient();
  return useMutation<UpdateUserPayload, Error, UpdateUserPayload>({
    mutationFn: (payload) => updateUser(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["user", id],
      });
      queryClient.invalidateQueries({
        queryKey: ["users"],
      });
    },
  });
}
