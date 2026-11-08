import { LONG_DESTINATION_AUTH_TIMEOUT_MS } from "@/constants/api-timeouts";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

interface TestDestinationResponse {
  message?: string;
  auth_url?: string;
  success?: boolean;
}

export type TestDestinationPayload = Record<string, unknown>;

const SUPPRESS_GLOBAL_ERROR_HEADER = { "X-Suppress-Global-Error-Toast": "1" };

const triggerDestination = async (
  destinationId: string,
  payload?: TestDestinationPayload,
): Promise<TestDestinationResponse> => {
  const response = await AxiosInstance.post<TestDestinationResponse>(
    ServerRoutes.destination.testDestination(Number(destinationId)),
    payload ?? {},
    {
      headers: SUPPRESS_GLOBAL_ERROR_HEADER,
      timeout: LONG_DESTINATION_AUTH_TIMEOUT_MS,
    },
  );

  if (response.data.success === false) {
    throw response.data;
  }

  return response.data;
};

export default function useTriggerDestination(destinationId: string) {
  const queryClient = useQueryClient();

  return useMutation<
    TestDestinationResponse,
    unknown,
    TestDestinationPayload | undefined
  >({
    mutationFn: (payload) => triggerDestination(destinationId, payload),
    onSuccess: (response: TestDestinationResponse) => {
      queryClient.invalidateQueries({
        queryKey: ["destinations", "allUserCreatedDestinationList"],
      });
      return response;
    },
  });
}
