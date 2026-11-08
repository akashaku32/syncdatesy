import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import {
  type AccountProfile,
  type UpdateAccountProfilePayload,
} from "@/types/accountProfile";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const updateAccountProfile = async (
  payload: UpdateAccountProfilePayload,
): Promise<AccountProfile> => {
  const response = await AxiosInstance.put(
    ServerRoutes.account.profileUpdate(),
    payload,
  );
  return response.data;
};

export default function useUpdateAccountProfile() {
  const queryClient = useQueryClient();

  return useMutation<AccountProfile, Error, UpdateAccountProfilePayload>({
    mutationFn: (payload) => updateAccountProfile(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["AccountProfile"] });
    },
  });
}
