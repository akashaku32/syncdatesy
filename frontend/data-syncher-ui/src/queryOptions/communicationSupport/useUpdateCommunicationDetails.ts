import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import {
  type CommunicationSupport,
  type CommunicationSupportUpdate,
} from "@/types/communicationSupport";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface ServerResponse {
  data: CommunicationSupport;
}

const updateCommunicationSupport = async (
  payload: CommunicationSupportUpdate,
): Promise<CommunicationSupport> => {
  const { data } = await AxiosInstance.put<ServerResponse>(
    ServerRoutes.communicationSupport.update(),
    payload,
  );
  return data.data;
};

export default function useUpdateCommunicationSupport() {
  const queryClient = useQueryClient();
  return useMutation<CommunicationSupport, Error, CommunicationSupportUpdate>({
    mutationFn: updateCommunicationSupport,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["communicationSupport"],
      });
    },
  });
}
