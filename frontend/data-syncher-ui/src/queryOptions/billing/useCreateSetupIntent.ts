import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation } from "@tanstack/react-query";

type SetupIntentResponse = {
  client_secret?: string;
  clientSecret?: string;
};

const createSetupIntent = async (): Promise<{ clientSecret: string }> => {
  const { data } = await AxiosInstance.post(
    ServerRoutes.billing.createSetupIntent(),
    {},
  );
  const secret = String(
    (data as SetupIntentResponse)?.client_secret ??
      (data as SetupIntentResponse)?.clientSecret ??
      "",
  );
  if (!secret) {
    throw new Error("Missing client secret.");
  }
  return { clientSecret: secret };
};

export default function useCreateSetupIntent() {
  return useMutation<{ clientSecret: string }, Error>({
    mutationFn: createSetupIntent,
  });
}
