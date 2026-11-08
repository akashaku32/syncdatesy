import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type MonthlyUsageResponse } from "@/types/billing";

import { useQuery } from "@tanstack/react-query";

const fetchMonthlyUsage = async (companyId: number) => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.billing.monthlyUsage({ companyId }),
  );
  return data as MonthlyUsageResponse;
};

export default function useFetchMonthlyUsage({
  companyId,
  enabled = true,
}: {
  companyId: number;
  enabled?: boolean;
}) {
  return useQuery<MonthlyUsageResponse>({
    queryKey: ["MonthlyUsage", companyId],
    queryFn: () => fetchMonthlyUsage(companyId),
    enabled: enabled && !!companyId,
  });
}
