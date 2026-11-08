import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type UserProfile } from "@/types/user";

import { useQuery } from "@tanstack/react-query";

const fetchCurrentUserProfile = async () => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.user.getCurrentUserProfile(),
  );
  return data;
};

export default function useFetchCurrentUserProfile() {
  return useQuery<UserProfile>({
    queryKey: ["UserProfile"],
    queryFn: fetchCurrentUserProfile,
  });
}
