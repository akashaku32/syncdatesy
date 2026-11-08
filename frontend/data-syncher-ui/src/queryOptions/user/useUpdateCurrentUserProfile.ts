import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type UpdateCurrentUserPayload, type UserProfile } from "@/types/user";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const updateCurrentUserProfile = async (
  payload: UpdateCurrentUserPayload,
): Promise<UserProfile> => {
  const response = await AxiosInstance.put(
    ServerRoutes.user.updateCurrentUserProfile(),
    payload,
  );
  return response.data;
};

export function useUpdateCurrentUserProfile() {
  const queryClient = useQueryClient();

  return useMutation<UserProfile, Error, UpdateCurrentUserPayload>({
    mutationFn: (payload) => updateCurrentUserProfile(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["UserProfile"],
      });
    },
  });
}
