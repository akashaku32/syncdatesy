import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type UserRole } from "@/types/user";

import { useQuery } from "@tanstack/react-query";

const fetchUserRoleList = async () => {
  const { data } = await AxiosInstance.get(ServerRoutes.user.listUserRoles());
  return data.content;
};

export default function useFetchUserRoleList() {
  return useQuery<UserRole[]>({
    queryKey: ["UserRoleList"],
    queryFn: fetchUserRoleList,
  });
}
