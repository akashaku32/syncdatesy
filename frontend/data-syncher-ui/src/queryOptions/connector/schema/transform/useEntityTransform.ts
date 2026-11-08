import ServerRoutes from "@/constants/server-routes";
import {
  TRANSFORM_DRY_RUN_TIMEOUT_MS,
  TRANSFORM_VALIDATE_TIMEOUT_MS,
} from "@/constants/transform-limits";
import AxiosInstance from "@/lib/axios/api-client";
import {
  type EntityTransformDetail,
  type TransformDryRunResponse,
  type TransformValidateResponse,
} from "@/types/connectors";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export const transformQueryKey = (connectionId: number, tableName: string) =>
  ["entityTransform", connectionId, tableName] as const;

export type EntityTransformGetResponse = {
  active: EntityTransformDetail | null;
  draft: EntityTransformDetail | null;
  summary: Record<string, unknown>;
  history: Array<Record<string, unknown>>;
};

export function useEntityTransform(connectionId: number, tableName: string) {
  return useQuery({
    queryKey: transformQueryKey(connectionId, tableName),
    queryFn: async () => {
      const { data } = await AxiosInstance.get<EntityTransformGetResponse>(
        ServerRoutes.connector.entityTransform(connectionId, tableName),
      );
      return data;
    },
    enabled: Boolean(connectionId && tableName),
  });
}

export function useValidateTransform(connectionId: number, tableName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const { data } = await AxiosInstance.post<TransformValidateResponse>(
        ServerRoutes.connector.entityTransformValidate(connectionId, tableName),
        payload,
        { timeout: TRANSFORM_VALIDATE_TIMEOUT_MS },
      );
      return data;
    },
    onSuccess: (data) => {
      if (data.published || data.draft) {
        queryClient.invalidateQueries({
          queryKey: transformQueryKey(connectionId, tableName),
        });
        queryClient.invalidateQueries({
          queryKey: ["ConnectorTable", connectionId],
        });
      }
    },
  });
}

export function useDryRunTransform(connectionId: number, tableName: string) {
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const { data } = await AxiosInstance.post<TransformDryRunResponse>(
        ServerRoutes.connector.entityTransformDryRun(connectionId, tableName),
        payload,
        { timeout: TRANSFORM_DRY_RUN_TIMEOUT_MS },
      );
      return data;
    },
  });
}

/** Validate and publish source-side Python without SAP mapping analysis. */
export function useValidatePublishSourceScript(
  connectionId: number,
  tableName: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const { data } = await AxiosInstance.post<TransformValidateResponse>(
        ServerRoutes.connector.sourceScriptValidatePublish(
          connectionId,
          tableName,
        ),
        payload,
        { timeout: TRANSFORM_VALIDATE_TIMEOUT_MS },
      );
      return data;
    },
    onSuccess: (data) => {
      if (data.published || data.draft) {
        queryClient.invalidateQueries({
          queryKey: transformQueryKey(connectionId, tableName),
        });
        queryClient.invalidateQueries({
          queryKey: ["ConnectorTable", connectionId],
        });
        queryClient.invalidateQueries({
          queryKey: ["ReverseSchema", connectionId],
        });
      }
    },
  });
}

/** Run the source-side Python script against a bounded Snowflake sample. */
export function useDryRunSourceScript(connectionId: number, tableName: string) {
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const { data } = await AxiosInstance.post<TransformDryRunResponse>(
        ServerRoutes.connector.sourceScriptDryRun(connectionId, tableName),
        payload,
        { timeout: TRANSFORM_DRY_RUN_TIMEOUT_MS },
      );
      return data;
    },
  });
}

/** Autosave draft (never activates). */
export function useSaveDraftTransform(connectionId: number, tableName: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      const { data } = await AxiosInstance.post<EntityTransformDetail>(
        ServerRoutes.connector.entityTransform(connectionId, tableName),
        { ...payload, activate: false },
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: transformQueryKey(connectionId, tableName),
      });
    },
  });
}

/** @deprecated Prefer useSaveDraftTransform — save no longer activates by default. */
export function useSaveTransform(connectionId: number, tableName: string) {
  return useSaveDraftTransform(connectionId, tableName);
}

export function useDeactivateTransform(
  connectionId: number,
  tableName: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await AxiosInstance.delete(
        ServerRoutes.connector.entityTransform(connectionId, tableName),
      );
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: transformQueryKey(connectionId, tableName),
      });
      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", connectionId],
      });
    },
  });
}
