import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

const deleteDestination = (destinationId: number) =>
  AxiosInstance.delete(
    ServerRoutes.destination.deleteDestination(destinationId),
  );

const useDeleteDestination = ({ destinationId }: { destinationId: number }) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => deleteDestination(destinationId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["destinations", "allUserCreatedDestinationList"],
      });
      queryClient.invalidateQueries({
        queryKey: ["destination", destinationId],
      });
    },
  });
};

export default useDeleteDestination;
