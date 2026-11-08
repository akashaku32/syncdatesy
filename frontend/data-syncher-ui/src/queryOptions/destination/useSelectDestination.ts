import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation } from "@tanstack/react-query";

interface SelectDestinationPayload {
  destination: number;
}

interface SelectDestinationResponse {
  message: string;
}

const selectDestination = async (
  payload: SelectDestinationPayload,
): Promise<SelectDestinationResponse> => {
  const { data } = await AxiosInstance.post<SelectDestinationResponse>(
    ServerRoutes.destination.selectDestination(),
    payload,
  );
  return data;
};

export default function useSelectDestination() {
  return useMutation<
    SelectDestinationResponse,
    Error,
    SelectDestinationPayload
  >({
    mutationFn: selectDestination,
  });
}
