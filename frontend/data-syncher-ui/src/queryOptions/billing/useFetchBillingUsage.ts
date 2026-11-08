import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useQuery } from "@tanstack/react-query";

export interface BillingUsageResponse {
  [key: string]: unknown;
}

const fetchBillingUsage = async (companyId: number, billingPeriod?: string) => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.billing.usage({ companyId, billingPeriod }),
  );
  return data as BillingUsageResponse;
};

export default function useFetchBillingUsage({
  companyId,
  billingPeriod,
  enabled = true,
}: {
  companyId: number;
  billingPeriod?: string;
  enabled?: boolean;
}) {
  return useQuery<BillingUsageResponse>({
    queryKey: ["BillingUsage", companyId, billingPeriod],
    queryFn: () => fetchBillingUsage(companyId, billingPeriod),
    enabled: enabled && !!companyId,
  });
}
