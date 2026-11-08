import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type CreateConnectionPayload } from "@/types/connectors";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface CreateConnectionResponse {
  message: string;
  auth_url?: string;
}

const createConnection = async (
  type: string,
  payload: CreateConnectionPayload,
): Promise<CreateConnectionResponse> => {
  const { data } = await AxiosInstance.post<CreateConnectionResponse>(
    ServerRoutes.connector.createConnector(type),
    payload,
  );
  return data;
};

export default function useCreateConnection(type: string) {
  const queryClient = useQueryClient();
  return useMutation<CreateConnectionResponse, Error, CreateConnectionPayload>({
    mutationFn: (payload) => createConnection(type, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["connectors"],
      });
    },
  });
}
