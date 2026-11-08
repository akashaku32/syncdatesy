import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type FieldConfig } from "@/types/form";

import { useQuery } from "@tanstack/react-query";

const fetchMasterSourceList = async ({
  type,
  source,
}: {
  type: string;
  source: "destinations" | "source";
}) => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.common.fetchFormSchema({ type, source }),
  );
  return source === "source" ? data.schema : data.fields;
};

export default function useFetchFormSchema({
  type,
  source,
}: {
  type: string;
  source: "destinations" | "source";
}) {
  return useQuery<FieldConfig[]>({
    queryKey: ["FormFieldInputs", type, source],
    queryFn: () => fetchMasterSourceList({ type, source }),
    enabled: !!type && !!source,
  });
}
