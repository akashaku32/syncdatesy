import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

type UpdateSelectedFieldsPayload = {
  tableName: string;
  selected_fields: string[];
};

const updateSelectedFields = (
  connectorId: number,
  payload: UpdateSelectedFieldsPayload,
) =>
  AxiosInstance.patch(
    ServerRoutes.connector.updateSelectedFields(connectorId, payload.tableName),
    { selected_fields: payload.selected_fields },
  );

const useUpdateSelectedFields = ({ connectorId }: { connectorId: number }) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: UpdateSelectedFieldsPayload) =>
      updateSelectedFields(connectorId, payload),

    onSuccess: async () => {
      queryClient.invalidateQueries({
        queryKey: ["ReverseSchema", connectorId],
      });
    },
  });
};

export default useUpdateSelectedFields;
