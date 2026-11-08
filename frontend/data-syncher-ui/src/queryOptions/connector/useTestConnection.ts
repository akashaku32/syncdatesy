import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation } from "@tanstack/react-query";

const testConnection = (connectorId: number) =>
  AxiosInstance.post(ServerRoutes.connector.testStatus(connectorId));

const useTestConnection = ({ connectorId }: { connectorId: number }) =>
  useMutation({
    mutationFn: () => testConnection(connectorId),
  });

export default useTestConnection;
