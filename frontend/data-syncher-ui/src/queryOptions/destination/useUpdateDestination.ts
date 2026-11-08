import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import {
  type DatabricksSparkJobProvisionOutcome,
  type Destination,
} from "@/types/destination";
import { type ErrorResponseType } from "@/types/error";

import { useMutation, useQueryClient } from "@tanstack/react-query";

type UpdateDestinationResponse = DatabricksSparkJobProvisionOutcome &
  Destination & {
    message?: string;
    auth_url?: string;
  };

const updateDestination = async (
  id: string,
  payload: Destination | FormData,
): Promise<UpdateDestinationResponse> => {
  const { data } = await AxiosInstance.put(
    ServerRoutes.destination.updateDestination(id),
    payload,
  );
  return data;
};

export function useUpdateDestination({ id }: { id: string }) {
  const queryClient = useQueryClient();
  return useMutation<
    UpdateDestinationResponse,
    ErrorResponseType,
    Destination | FormData
  >({
    mutationFn: (payload) => updateDestination(id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["destinations", "allUserCreatedDestinationList"],
      });
    },
  });
}
