import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { type Source } from "@/types/source";

import { useQuery } from "@tanstack/react-query";

const fetchMasterSourceList = async () => {
  const { data } = await AxiosInstance.get(
    ServerRoutes.source.listMasterSources(),
  );
  return data.content;
};

export default function useFetchMasterSourceList() {
  return useQuery<Source[]>({
    queryKey: ["SourceList"],
    queryFn: fetchMasterSourceList,
  });
}
