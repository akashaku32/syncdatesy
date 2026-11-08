import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useQuery } from "@tanstack/react-query";

export interface ConnectedConnector {
  id: number;
  name: string;
}

export interface CompanyProxyAgentStatusResponse {
  has_agent: boolean;
  agent_uuid?: string;
  agent_name?: string;
  created_at?: string;
  connected_connectors?: ConnectedConnector[];
}

const fetchCompanyProxyAgentStatus =
  async (): Promise<CompanyProxyAgentStatusResponse> => {
    const { data } = await AxiosInstance.get<CompanyProxyAgentStatusResponse>(
      ServerRoutes.proxyAgent.status(),
    );
    return data;
  };

export default function useFetchCompanyProxyAgent(enabled = true) {
  return useQuery<CompanyProxyAgentStatusResponse>({
    queryKey: ["companyProxyAgentStatus"],
    queryFn: fetchCompanyProxyAgentStatus,
    enabled,
    staleTime: 10000,
  });
}
