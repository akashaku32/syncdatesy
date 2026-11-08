import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

type UpdateTableEmailGroupsPayload = {
  tableName: string;
  notification_email_group_ids: number[];
  email_template_id?: number | string | null;
  email_custom_fields?: {
    subject?: string;
    subject_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    body_fields?: string[];
    greeting_name?: string;
    greeting_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    body_content?: string;
    body_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    team_name?: string;
    team_styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
    styles?: {
      bold?: boolean;
      italic?: boolean;
      color?: string;
      font_family?: string;
      font_size?: string;
    } | null;
  };
};

const updateTableEmailGroups = (
  connectorId: number,
  payload: UpdateTableEmailGroupsPayload,
) =>
  AxiosInstance.patch(
    ServerRoutes.connector.updateTableEmailGroups(
      connectorId,
      payload.tableName,
    ),
    {
      notification_email_group_ids: payload.notification_email_group_ids,
      email_template_id: payload.email_template_id,
      email_custom_fields: payload.email_custom_fields,
    },
  );

const useUpdateTableEmailGroups = ({
  connectorId,
}: {
  connectorId: number;
}) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: UpdateTableEmailGroupsPayload) =>
      updateTableEmailGroups(connectorId, payload),

    onSuccess: async () => {
      queryClient.invalidateQueries({
        queryKey: ["ReverseSchema", connectorId],
      });
    },
  });
};

export default useUpdateTableEmailGroups;
