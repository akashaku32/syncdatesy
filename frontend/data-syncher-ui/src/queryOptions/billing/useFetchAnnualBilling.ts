import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type YearlyBillingData } from "@/types/billing";

import { useQuery } from "@tanstack/react-query";

const fetchAnnualBilling = async (companyId: number) => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.billing.listAnnualBilling({ companyId: String(companyId) }),
  );
  return data;
};

export default function useFetchAnnualBilling({
  companyId,
  enabled = true,
}: {
  companyId: number;
  enabled?: boolean;
}) {
  return useQuery<YearlyBillingData>({
    queryKey: ["AnnualBilling", companyId],
    queryFn: () => fetchAnnualBilling(companyId),
    enabled: enabled && !!companyId,
  });
}
