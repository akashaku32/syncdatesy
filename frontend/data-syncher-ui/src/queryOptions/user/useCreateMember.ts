import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type CreateUserPayload } from "@/types/user";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface CreateUserResponse {
  message: string;
}

const createUser = async (
  payload: CreateUserPayload,
): Promise<CreateUserResponse> => {
  const { data } = await AxiosInstance.post<CreateUserResponse>(
    ServerRoutes.user.createUser(),
    payload,
  );
  return data;
};

export default function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation<CreateUserResponse, Error, CreateUserPayload>({
    mutationFn: createUser,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["users"],
      });
    },
  });
}
