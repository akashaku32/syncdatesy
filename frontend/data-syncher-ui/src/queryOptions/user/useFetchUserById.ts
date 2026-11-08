import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type User } from "@/types/user";

import { UseQueryResult, useQuery } from "@tanstack/react-query";

/**
 * Fetch a user by their ID.
 * @param id - The user ID.
 * @returns The user data.
 */

const fetchUserById = async (id: number): Promise<User> => {
  const { data } = await AxiosInstance.get<User>(
    ServerRoutes.user.fetchUserById(id),
  );
  return data;
};

const useFetchUserById = (id: number): UseQueryResult<User> => {
  return useQuery({
    queryKey: ["user", id],
    queryFn: () => fetchUserById(id),
    enabled: Boolean(id),
  });
};

export default useFetchUserById;
