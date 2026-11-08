import { AxiosError, AxiosResponse } from "axios";

import { toaster } from "@/components/ui/toaster";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface ToggleStatusResponse {
  connection_id: number;
  new_status: string;
  message: string;
}

const toggleConnectionStatus = (
  connectorId: number,
): Promise<AxiosResponse<ToggleStatusResponse>> =>
  AxiosInstance.post(ServerRoutes.connector.toggleStatus(connectorId));

const useToggleConnectionStatus = ({
  connectorId,
}: {
  connectorId: number;
}) => {
  const queryClient = useQueryClient();

  return useMutation<AxiosResponse<ToggleStatusResponse>, AxiosError>({
    mutationFn: () => toggleConnectionStatus(connectorId),

    onSuccess: (response) => {
      if (response?.data?.message) {
        toaster.create({
          description: response.data.message,
          type: "success",
        });
      }

      queryClient.invalidateQueries({
        queryKey: ["connector", connectorId],
      });
    },
  });
};

export default useToggleConnectionStatus;
