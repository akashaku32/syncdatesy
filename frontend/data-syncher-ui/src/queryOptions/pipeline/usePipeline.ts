import {
  PIPELINE_RUN_LIVE_REFETCH_MS,
  isLivePipelineRunStatus,
  pipelineRunRefetchInterval,
  resolvePipelineRunStatus,
} from "@/components/dashboard/components/Scheduling/pipelineRunHelpers";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import {
  type AddPipelineNodePayload,
  type CreatePipelinePayload,
  type PatchPipelineNodePayload,
  type PatchPipelinePayload,
  type PipelineConnectionItem,
  type PipelineDetail,
  type PipelineEdgeDto,
  type PipelineNodeDto,
  type PipelineRunDetail,
  type PipelineRunSummary,
  type PipelineValidationResult,
} from "@/types/pipeline";

import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

export const pipelineConnectionsQueryKey = ["pipelineConnections"] as const;
export const pipelinesQueryKey = ["pipelines"] as const;

function updatePipelineInCache(
  queryClient: QueryClient,
  pipelineId: number,
  updater: (_pipeline: PipelineDetail) => PipelineDetail,
) {
  queryClient.setQueryData<PipelineDetail[]>(pipelinesQueryKey, (pipelines) => {
    if (!pipelines) return pipelines;
    return pipelines.map((p) => (p.id === pipelineId ? updater(p) : p));
  });
}

async function refreshPipelines(queryClient: QueryClient) {
  await queryClient.invalidateQueries({ queryKey: pipelinesQueryKey });
}

async function fetchPipelineRuns(pipelineId: number) {
  const { data } = await AxiosInstance.get<{ runs: PipelineRunSummary[] }>(
    ServerRoutes.pipeline.runs(pipelineId),
  );
  return { runs: data.runs ?? [] };
}

async function fetchPipelineRun(pipelineId: number, runId: number) {
  const { data } = await AxiosInstance.get<PipelineRunDetail>(
    ServerRoutes.pipeline.runDetail(pipelineId, runId),
  );
  return data;
}

export function prefetchPipelineRuns(
  queryClient: QueryClient,
  pipelineId: number,
) {
  return queryClient.prefetchQuery({
    queryKey: pipelineRunsQueryKey(pipelineId),
    queryFn: () => fetchPipelineRuns(pipelineId),
    staleTime: 30 * 1000,
  });
}

export function prefetchPipelineRun(
  queryClient: QueryClient,
  pipelineId: number,
  runId: number,
) {
  return queryClient.prefetchQuery({
    queryKey: pipelineRunQueryKey(pipelineId, runId),
    queryFn: () => fetchPipelineRun(pipelineId, runId),
    staleTime: 30 * 1000,
  });
}

export function usePipelineConnections() {
  return useQuery<PipelineConnectionItem[]>({
    queryKey: pipelineConnectionsQueryKey,
    queryFn: async () => {
      const { data } = await AxiosInstance.get<{
        connections: PipelineConnectionItem[];
      }>(ServerRoutes.pipeline.connections());
      return data.connections ?? [];
    },
    staleTime: 60 * 1000,
  });
}

export function usePipelines() {
  return useQuery<PipelineDetail[]>({
    queryKey: pipelinesQueryKey,
    queryFn: async () => {
      const { data } = await AxiosInstance.get<{ pipelines: PipelineDetail[] }>(
        ServerRoutes.pipeline.list(),
      );
      return data.pipelines ?? [];
    },
    staleTime: 15 * 1000,
    refetchInterval: (query) =>
      query.state.data?.some((p) =>
        isLivePipelineRunStatus(p.latest_run_status),
      )
        ? PIPELINE_RUN_LIVE_REFETCH_MS
        : false,
  });
}

export function useCreatePipeline() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["createPipeline"],
    mutationFn: async (payload: CreatePipelinePayload) => {
      const { data } = await AxiosInstance.post<PipelineDetail>(
        ServerRoutes.pipeline.list(),
        payload,
      );
      return data;
    },
    onSuccess: (pipeline) => {
      queryClient.setQueryData<PipelineDetail[]>(
        pipelinesQueryKey,
        (pipelines) => [...(pipelines ?? []), pipeline],
      );
    },
  });
}

export function usePatchPipeline(pipelineId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["patchPipeline", pipelineId],
    mutationFn: async (payload: PatchPipelinePayload) => {
      const { data } = await AxiosInstance.patch<PipelineDetail>(
        ServerRoutes.pipeline.detail(pipelineId),
        payload,
      );
      return data;
    },
    onSuccess: (updated) => {
      updatePipelineInCache(queryClient, pipelineId, (p) => ({
        ...p,
        ...updated,
        nodes: p.nodes,
        edges: p.edges,
      }));
    },
  });
}

export function useDeletePipeline() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["deletePipeline"],
    mutationFn: async (pipelineId: number) => {
      await AxiosInstance.delete(ServerRoutes.pipeline.detail(pipelineId));
    },
    onSuccess: (_data, pipelineId) => {
      queryClient.setQueryData<PipelineDetail[]>(
        pipelinesQueryKey,
        (pipelines) => pipelines?.filter((p) => p.id !== pipelineId) ?? [],
      );
    },
  });
}

export function useAddPipelineNode(pipelineId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["addPipelineNode", pipelineId],
    mutationFn: async (payload: AddPipelineNodePayload) => {
      const { data } = await AxiosInstance.post<PipelineNodeDto>(
        ServerRoutes.pipeline.nodes(pipelineId),
        payload,
      );
      return data;
    },
    onSuccess: (node) => {
      updatePipelineInCache(queryClient, pipelineId, (p) => {
        const exists = p.nodes.some((n) => n.id === node.id);
        if (exists) return p;
        return { ...p, nodes: [...p.nodes, node] };
      });
    },
  });
}

export function useUpdatePipelineNode(pipelineId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["updatePipelineNode", pipelineId],
    mutationFn: async ({
      nodeId,
      payload,
    }: {
      nodeId: number;
      payload: PatchPipelineNodePayload;
    }) => {
      const { data } = await AxiosInstance.patch<PipelineNodeDto>(
        ServerRoutes.pipeline.nodeDetail(pipelineId, nodeId),
        payload,
      );
      return data;
    },
    onSuccess: (updated) => {
      updatePipelineInCache(queryClient, pipelineId, (p) => ({
        ...p,
        nodes: p.nodes.map((n) =>
          n.id === updated.id ? { ...n, ...updated } : n,
        ),
      }));
    },
  });
}

export function useDeletePipelineNode(pipelineId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["deletePipelineNode", pipelineId],
    mutationFn: async (nodeId: number) => {
      await AxiosInstance.delete(
        ServerRoutes.pipeline.nodeDetail(pipelineId, nodeId),
      );
      return nodeId;
    },
    onSuccess: (nodeId) => {
      updatePipelineInCache(queryClient, pipelineId, (p) => ({
        ...p,
        nodes: p.nodes.filter((n) => n.id !== nodeId),
        edges: p.edges.filter(
          (e) => e.from_node_id !== nodeId && e.to_node_id !== nodeId,
        ),
      }));
    },
  });
}

export function useAddPipelineEdge(pipelineId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["addPipelineEdge", pipelineId],
    mutationFn: async (payload: {
      from_node_id: number;
      to_node_id: number;
    }) => {
      const { data } = await AxiosInstance.post<PipelineEdgeDto>(
        ServerRoutes.pipeline.edges(pipelineId),
        payload,
      );
      return data;
    },
    onSuccess: (edge) => {
      updatePipelineInCache(queryClient, pipelineId, (p) => {
        const exists = p.edges.some((e) => e.id === edge.id);
        if (exists) return p;
        return { ...p, edges: [...p.edges, edge] };
      });
    },
  });
}

export function useDeletePipelineEdge(pipelineId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["deletePipelineEdge", pipelineId],
    mutationFn: async (edgeId: number) => {
      await AxiosInstance.delete(
        ServerRoutes.pipeline.edgeDetail(pipelineId, edgeId),
      );
      return edgeId;
    },
    onSuccess: (edgeId) => {
      updatePipelineInCache(queryClient, pipelineId, (p) => ({
        ...p,
        edges: p.edges.filter((e) => e.id !== edgeId),
      }));
    },
  });
}

export function useRunPipeline() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["runPipeline"],
    mutationFn: async (pipelineId: number) => {
      const { data } = await AxiosInstance.post<{
        message: string;
        pipeline_run_id: number;
      }>(ServerRoutes.pipeline.run(pipelineId), {});
      return data;
    },
    onSuccess: async (data, pipelineId) => {
      await prefetchPipelineRun(queryClient, pipelineId, data.pipeline_run_id);
      await queryClient.invalidateQueries({
        queryKey: ["pipelineRuns", pipelineId],
      });
      await queryClient.invalidateQueries({ queryKey: pipelinesQueryKey });
    },
  });
}

export function useValidatePipeline() {
  return useMutation({
    mutationKey: ["validatePipeline"],
    mutationFn: async (pipelineId: number) => {
      const { data } = await AxiosInstance.post<PipelineValidationResult>(
        ServerRoutes.pipeline.validate(pipelineId),
        {},
        {
          // Validation failures return 400 with a structured errors[] body.
          validateStatus: (status) => status === 200 || status === 400,
        },
      );
      return data;
    },
  });
}

export function useResetPipeline() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationKey: ["resetPipeline"],
    mutationFn: async (pipelineId: number) => {
      const { data } = await AxiosInstance.post<PipelineValidationResult>(
        ServerRoutes.pipeline.reset(pipelineId),
        {},
      );
      return data;
    },
    onSuccess: async (_data, pipelineId) => {
      await refreshPipelines(queryClient);
      await queryClient.invalidateQueries({
        queryKey: ["pipelineRuns", pipelineId],
      });
    },
  });
}

export function pipelineRunsQueryKey(pipelineId: number) {
  return ["pipelineRuns", pipelineId] as const;
}

export function usePipelineRuns(pipelineId: number | null) {
  return useQuery<{ runs: PipelineRunSummary[] }>({
    queryKey: pipelineRunsQueryKey(pipelineId ?? 0),
    queryFn: () => fetchPipelineRuns(pipelineId!),
    enabled: !!pipelineId,
    // Must poll while queued too — otherwise the run picker stays stuck on
    // "queued" after the worker has already started (no pipeline-run WebSocket).
    refetchInterval: (query) =>
      query.state.data?.runs.some((run) =>
        isLivePipelineRunStatus(resolvePipelineRunStatus(run)),
      )
        ? PIPELINE_RUN_LIVE_REFETCH_MS
        : false,
    staleTime: 15 * 1000,
  });
}

export function pipelineRunQueryKey(
  pipelineId: number,
  runId: number | null | undefined,
) {
  return ["pipelineRun", pipelineId, runId] as const;
}

export function usePipelineRun(
  pipelineId: number | null,
  runId: number | null,
) {
  return useQuery<PipelineRunDetail>({
    queryKey: pipelineRunQueryKey(pipelineId ?? 0, runId),
    queryFn: () => fetchPipelineRun(pipelineId!, runId!),
    enabled: !!pipelineId && !!runId,
    refetchInterval: (query) => {
      const status = query.state.data
        ? resolvePipelineRunStatus(query.state.data)
        : undefined;
      const live = pipelineRunRefetchInterval(status);
      if (live) return live;
      // After the run ends, poll briefly so nested migration_status catches up
      // (tables may still show in_progress until the final fail rows are written).
      if (
        status &&
        ["failed", "completed", "timeout"].includes(status) &&
        Date.now() - query.state.dataUpdatedAt < 20_000
      ) {
        return PIPELINE_RUN_LIVE_REFETCH_MS;
      }
      return false;
    },
    staleTime: 5 * 1000,
  });
}
