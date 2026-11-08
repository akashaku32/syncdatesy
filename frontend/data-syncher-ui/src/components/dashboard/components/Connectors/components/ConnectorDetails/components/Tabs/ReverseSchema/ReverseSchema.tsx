import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Flex, Grid } from "@chakra-ui/react";

import { Navigate, useOutletContext } from "react-router";

import LoadingSpinner from "@/components/shared/Spinner";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import useFetchReverseSchema from "@/queryOptions/connector/reverseSchema/useFetchReverseSchema";
import {
  batchesQueryKey,
  useFetchBatches,
} from "@/queryOptions/connector/schema/useBatches";
import useFetchTableStatus from "@/queryOptions/connector/schema/useFetchTableStatus";
import useUpdateSchemaStatus from "@/queryOptions/connector/schema/useUpdateSchemaStatus";
import { type Connector, type UnassignedTable } from "@/types/connectors";
import { type TableMappingDTO } from "@/types/mappings";

import { isSnowflakeToSnowflakeConnector } from "../../../helpers";
import BatchGroupedPanel from "../Schema/Batches/BatchGroupedPanel";
import Actions from "./Actions";
import Destination from "./components/Destination/Destination";
import FileExportSchema from "./components/FileExportSchema/FileExportSchema";
import Mapped, { type MappedRef } from "./components/Mapped/Mapped";
import Source from "./components/Source/Source";
import { useIsMutating, useQuery, useQueryClient } from "@tanstack/react-query";

const normalizeMappedPairs = (
  raw: unknown,
): { source: string; destination?: string }[] => {
  const list =
    (Array.isArray((raw as { mappings?: TableMappingDTO[] })?.mappings)
      ? (raw as { mappings: TableMappingDTO[] }).mappings
      : Array.isArray(raw)
        ? (raw as TableMappingDTO[])
        : Array.isArray((raw as { data?: TableMappingDTO[] })?.data)
          ? (raw as { data: TableMappingDTO[] }).data
          : []) ?? [];

  const pairs: { source: string; destination?: string }[] = [];
  const seen = new Set<string>();
  for (const item of list) {
    const source =
      item.source_table ?? (item as { sourceTable?: string }).sourceTable;
    if (!source || seen.has(source.toLowerCase())) continue;
    seen.add(source.toLowerCase());
    const destination =
      item.destination_table ??
      (item as { destinationTable?: string }).destinationTable;
    pairs.push({ source, destination: destination || undefined });
  }
  return pairs;
};

const ReverseSchema = () => {
  const context = useOutletContext<Connector>();
  const mappedRef = useRef<MappedRef>(null);
  const queryClient = useQueryClient();

  const [shouldShowDisabledState, setShouldShowDisabledState] = useState(false);
  const [fileExportSelectedTables, setFileExportSelectedTables] = useState<
    string[]
  >([]);
  const [fileExportSelectionDirty, setFileExportSelectionDirty] =
    useState(false);

  const { data: reverseSchemaData, isLoading } = useFetchReverseSchema(
    context.connection_id,
  );

  const { data: tableStatusData } = useFetchTableStatus(
    context.connection_id,
    true,
  );

  const { data: batchesData } = useFetchBatches(context.connection_id);

  const { status: schemaStatus } = useUpdateSchemaStatus(
    context.connection_id,
    true,
  );

  const isCheckingSchemaStatus = !!schemaStatus?.is_in_progress;
  const prevIsCheckingRef = useRef(false);

  useEffect(() => {
    if (isCheckingSchemaStatus && !prevIsCheckingRef.current) {
      prevIsCheckingRef.current = true;
    } else if (!isCheckingSchemaStatus && prevIsCheckingRef.current) {
      prevIsCheckingRef.current = false;
      queryClient.invalidateQueries({
        queryKey: ["ReverseSchema", context.connection_id],
      });
      queryClient.refetchQueries({
        queryKey: ["ReverseSchema", context.connection_id],
      });
    } else if (!isCheckingSchemaStatus) {
      prevIsCheckingRef.current = false;
    }
  }, [isCheckingSchemaStatus, context.connection_id, queryClient]);

  const isRefreshSchemaInProgress = useIsMutating({
    mutationKey: ["refreshSchema", context.connection_id],
  });

  const isUpdateSchemaInProgress = useIsMutating({
    mutationKey: ["updateSchema", context.connection_id],
  });

  const isMigrationInProgress = useMemo(() => {
    const hasTableInProgress =
      tableStatusData?.tables?.some(
        (t: { status: string | null }) => t.status === "in_progress",
      ) || false;
    const isSchemaSyncing = schemaStatus?.is_in_progress === true;
    const isRefreshing = isRefreshSchemaInProgress > 0;
    const isUpdating = isUpdateSchemaInProgress > 0;
    const isGlobalRefreshActive =
      tableStatusData?.schema_refresh_in_progress === true;

    return (
      hasTableInProgress ||
      isSchemaSyncing ||
      isRefreshing ||
      isUpdating ||
      isGlobalRefreshActive
    );
  }, [
    tableStatusData,
    schemaStatus,
    isRefreshSchemaInProgress,
    isUpdateSchemaInProgress,
  ]);

  const totalDisabledState = shouldShowDisabledState || isMigrationInProgress;
  const isSnowflakeToSnowflake = isSnowflakeToSnowflakeConnector(context);
  const isSnowflakeToFileExport =
    context.source_name?.toLowerCase() === "snowflake" &&
    !!context.is_file_based;

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset file export selection when connection changes
    setFileExportSelectionDirty(false);
    setFileExportSelectedTables([]);
  }, [context.connection_id]);

  const { data: mappedPairs = [] } = useQuery({
    queryKey: ["connectionMappings", context.connection_id],
    queryFn: async () => {
      const { data } = await AxiosInstance.get(
        ServerRoutes.connector.fetchConnectionMappings(context.connection_id),
      );
      return normalizeMappedPairs(data);
    },
    enabled: !isSnowflakeToFileExport && !isSnowflakeToSnowflake,
    staleTime: 30 * 1000,
  });

  const tableToBatchName = useMemo<Map<string, string>>(() => {
    const map = new Map<string, string>();
    batchesData?.batches?.forEach((b) => {
      b.tables?.forEach((t) => {
        const name = t?.table_name;
        if (typeof name === "string" && name.length > 0) {
          map.set(name.toLowerCase(), b.name);
        }
      });
    });
    return map;
  }, [batchesData]);

  const savedFileExportSelected = useMemo(
    () =>
      reverseSchemaData?.source_tables
        ?.filter((item) => item.selected)
        .map((item) => item.table) ?? [],
    [reverseSchemaData?.source_tables],
  );

  const effectiveFileExportSelected = useMemo(
    () =>
      fileExportSelectionDirty
        ? fileExportSelectedTables
        : savedFileExportSelected,
    [
      fileExportSelectionDirty,
      fileExportSelectedTables,
      savedFileExportSelected,
    ],
  );

  const sourceToDestination = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of mappedPairs) {
      if (p.destination) {
        map.set(p.source.toLowerCase(), p.destination);
      }
    }
    return map;
  }, [mappedPairs]);

  const pendingUnassignedTables = useMemo<UnassignedTable[]>(() => {
    if (isSnowflakeToFileExport) {
      return effectiveFileExportSelected
        .filter((name) => !tableToBatchName.has(name.toLowerCase()))
        .map((name, index) => ({
          table_name: name,
          sequence: index,
        }));
    }

    return mappedPairs
      .filter((p) => !tableToBatchName.has(p.source.toLowerCase()))
      .map((p, index) => ({
        table_name: p.source,
        sequence: index,
        mapped_destination: p.destination,
      }));
  }, [
    effectiveFileExportSelected,
    isSnowflakeToFileExport,
    mappedPairs,
    tableToBatchName,
  ]);

  const handleFileExportSelectionChange = useCallback(
    (tables: string[], isDirty: boolean) => {
      setFileExportSelectedTables(tables);
      setFileExportSelectionDirty(isDirty);
    },
    [],
  );

  const invalidateMappingAndBatches = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["connectionMappings", context.connection_id],
    });
    queryClient.invalidateQueries({
      queryKey: batchesQueryKey(context.connection_id),
    });
  }, [context.connection_id, queryClient]);

  const handleMappingsChange = useCallback(
    (mappings: { sourceTable: string; destinationTable: string }[]) => {
      queryClient.setQueryData(
        ["connectionMappings", context.connection_id],
        mappings.map((m) => ({
          source: m.sourceTable,
          destination: m.destinationTable,
        })),
      );
      invalidateMappingAndBatches();
    },
    [context.connection_id, invalidateMappingAndBatches, queryClient],
  );

  const handleDrop = (sourceTable: string, destinationTable: string) => {
    mappedRef.current?.handleDrop(sourceTable, destinationTable);
  };

  const handleUnmapSource = useCallback((sourceTable: string) => {
    mappedRef.current?.removeBySourceTable(sourceTable);
  }, []);

  if (isLoading && !reverseSchemaData) {
    return <LoadingSpinner />;
  }

  if (isSnowflakeToSnowflake) {
    return <Navigate to={`../${ClientRoutes.CONNECTORS.SCHEMA}`} replace />;
  }

  return (
    <Flex flexDirection="column" gap={4} pb={8} w="100%">
      <Actions
        shouldShowDisabledState={totalDisabledState}
        setShouldShowDisabledState={setShouldShowDisabledState}
      />
      {isSnowflakeToFileExport ? (
        <FileExportSchema
          connector={context}
          reverseSchemaData={reverseSchemaData || null}
          isDisabled={totalDisabledState}
          tableToBatchName={tableToBatchName}
          onSelectionChange={handleFileExportSelectionChange}
        />
      ) : (
        <>
          <Grid
            templateColumns={["1fr", "1fr 1fr minmax(280px, 380px)"]}
            gap={4}
            w="100%"
            alignItems="start"
          >
            <Source
              connectionId={context.connection_id}
              reverseSchemaData={reverseSchemaData || null}
            />
            <Destination
              connectionId={context.connection_id}
              onDrop={handleDrop}
              reverseSchemaData={reverseSchemaData || null}
            />
            <BatchGroupedPanel
              connectionId={context.connection_id}
              pendingUnassignedTables={pendingUnassignedTables}
              flowHint="mapping"
              onUnmapSource={handleUnmapSource}
              sourceToDestination={sourceToDestination}
            />
          </Grid>
          {/* Mapping save/drop logic only — no visible Mapped column. */}
          <Mapped
            ref={mappedRef}
            reverseSchemaData={reverseSchemaData || null}
            isDisabled={totalDisabledState}
            onMappingsChange={handleMappingsChange}
            hideUi
          />
        </>
      )}
    </Flex>
  );
};

export default ReverseSchema;
