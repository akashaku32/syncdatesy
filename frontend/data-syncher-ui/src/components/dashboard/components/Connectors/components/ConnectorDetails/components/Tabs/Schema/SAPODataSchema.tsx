import { Suspense, lazy, useEffect, useMemo, useState } from "react";

import {
  ActionBar,
  Badge,
  Box,
  Button,
  Checkbox,
  CloseButton,
  Dialog,
  Flex,
  Grid,
  IconButton,
  Input,
  InputGroup,
  Portal,
  Skeleton,
  Text,
  VStack,
} from "@chakra-ui/react";

import { GoPlus } from "react-icons/go";
import {
  IoMdFunnel,
  IoMdOptions,
  IoMdPlay,
  IoMdSettings,
} from "react-icons/io";
import { IoCaretDownSharp } from "react-icons/io5";
import { MdSearch } from "react-icons/md";
import { TbDelta, TbTransform } from "react-icons/tb";

import { useOutletContext } from "react-router";

import LoadingSpinner from "@/components/shared/Spinner";
import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import { queryClient } from "@/lib/react-query-client";
import { useFetchBatches } from "@/queryOptions/connector/schema/useBatches";
import useFetchConnectorTableById from "@/queryOptions/connector/schema/useFetchTable";
import useFetchTableFields from "@/queryOptions/connector/schema/useFetchTableFields";
import useFetchTableStatus from "@/queryOptions/connector/schema/useFetchTableStatus";
import useUpdateSelectedTables from "@/queryOptions/connector/schema/useUpdateSelectedTables";
import {
  type Connector,
  type ConnectorTable,
  type ConnectorTablesResponse,
  type RowFilterConfig,
  type UnassignedTable,
} from "@/types/connectors";

import { isPrimaryKey } from "../ReverseSchema/utils/validation";
import Actions from "./Actions";
import BatchGroupedPanel from "./Batches/BatchGroupedPanel";
import RowFilterModal from "./RowFilterModal";
import TargetSettingsModal from "./TargetSettingsModal";
import { isConnectorTableMarkedSelected } from "./schemaSelection";
import {
  getEffectiveRowFilter,
  hasRowFilter as hasRowFilterUtil,
} from "./utils/filterUtils";

const TransformModal = lazy(() => import("./TransformModal/TransformModal"));

interface EntityGroup {
  entityName: string;
  tableItem: ConnectorTable;
}

interface ServiceGroup {
  serviceName: string;
  entities: EntityGroup[];
}

const getErrorMessage = (err: unknown, defaultMsg: string): string => {
  if (err && typeof err === "object") {
    if ("response" in err) {
      const response = (err as { response?: { data?: unknown } }).response;
      if (response && response.data) {
        const data = response.data;
        if (typeof data === "string") {
          return data;
        }
        if (typeof data === "object") {
          const objData = data as Record<string, unknown>;
          if (
            "message" in objData &&
            typeof objData.message === "string" &&
            objData.message
          ) {
            return objData.message;
          }
          if ("detail" in objData && objData.detail) {
            if (typeof objData.detail === "string") {
              try {
                const parsedDetail = JSON.parse(objData.detail) as Record<
                  string,
                  unknown
                >;
                if (parsedDetail && typeof parsedDetail === "object") {
                  if (
                    "detail" in parsedDetail &&
                    typeof parsedDetail.detail === "string" &&
                    parsedDetail.detail
                  ) {
                    return parsedDetail.detail;
                  }
                  if (
                    "message" in parsedDetail &&
                    typeof parsedDetail.message === "string" &&
                    parsedDetail.message
                  ) {
                    return parsedDetail.message;
                  }
                }
              } catch {
                // Ignore parsing errors and fallback to raw detail string
              }
              return objData.detail;
            }
            if (typeof objData.detail === "object" && objData.detail) {
              const detailObj = objData.detail as Record<string, unknown>;
              if (
                "message" in detailObj &&
                typeof detailObj.message === "string" &&
                detailObj.message
              ) {
                return detailObj.message;
              }
            }
          }
        }
      }
    }
    if (
      "message" in err &&
      typeof (err as { message?: unknown }).message === "string"
    ) {
      return (err as { message: string }).message;
    }
  }
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return defaultMsg;
};

const isFilterableColumn = (info: unknown): boolean => {
  if (typeof info === "object" && info !== null) {
    const fieldObj = info as Record<string, unknown>;
    return Boolean(
      fieldObj.filterable ?? fieldObj.is_filterable ?? fieldObj.isFilterable,
    );
  }
  return false;
};

const ColumnList = ({
  tableName,
  connectionId,
}: {
  tableName: string;
  connectionId: number;
  userSelectedFields?: Record<string, string[]>;
  setUserSelectedFields?: React.Dispatch<
    React.SetStateAction<Record<string, string[]>>
  >;
  setHasChanged?: React.Dispatch<React.SetStateAction<boolean>>;
  setSelectedTables?: React.Dispatch<React.SetStateAction<string[]>>;
}) => {
  const { data: fieldsData, isLoading } = useFetchTableFields(
    connectionId,
    tableName,
    true,
  );

  const fields = useMemo(() => {
    if (!fieldsData?.table_fields) return [];
    return Object.entries(fieldsData.table_fields).sort(
      ([nameA, infoA], [nameB, infoB]) => {
        const isPKA = isPrimaryKey(nameA, infoA);
        const isPKB = isPrimaryKey(nameB, infoB);
        if (isPKA && !isPKB) return -1;
        if (!isPKA && isPKB) return 1;
        return nameA.localeCompare(nameB);
      },
    );
  }, [fieldsData]);

  if (isLoading) {
    return (
      <VStack align="stretch" pl={8} gap={1} py={1}>
        <Skeleton height={5} width="150px" />
        <Skeleton height={5} width="150px" />
      </VStack>
    );
  }

  return (
    <VStack align="stretch" pl={8} gap={1} py={1}>
      {fields.map(([name, info]) => {
        const isPK = isPrimaryKey(name, info);
        const isFilterable = isFilterableColumn(info);
        const dataType =
          typeof info === "string"
            ? info
            : (info as { data_type?: string }).data_type || "unknown";

        return (
          <Flex key={name} align="center" gap={2}>
            {isPK && (
              <Text fontSize="xs" color="yellow.600">
                🔑
              </Text>
            )}
            {isFilterable && (
              <Tooltip content="Filterable column">
                <Box
                  display="inline-flex"
                  alignItems="center"
                  color="brand.600"
                >
                  <IoMdFunnel size={14} />
                </Box>
              </Tooltip>
            )}
            <Text
              fontSize="sm"
              fontWeight={isPK ? "bold" : "normal"}
              color="gray.800"
            >
              {name}
            </Text>
            <Text color="gray.500">:</Text>
            <Text fontSize="sm" color="gray.600">
              {dataType}
            </Text>
          </Flex>
        );
      })}
    </VStack>
  );
};

const EntityAccordion = ({
  entity,
  connectionId,
  selectedTables,
  setSelectedTables,
  userSelectedFields,
  setUserSelectedFields,
  setHasChanged,
  batchName,
  selectionLocked = false,
  isSaving,
  isAssigningTables,
  onOpenSettings,
  onOpenRowFilter,
  onOpenTransform,
}: {
  entity: EntityGroup;
  connectionId: number;
  selectedTables: string[];
  setSelectedTables: React.Dispatch<React.SetStateAction<string[]>>;
  userSelectedFields: Record<string, string[]>;
  setUserSelectedFields: React.Dispatch<
    React.SetStateAction<Record<string, string[]>>
  >;
  setHasChanged: React.Dispatch<React.SetStateAction<boolean>>;
  batchName?: string | null;
  selectionLocked?: boolean;
  isSaving: boolean;
  isAssigningTables: boolean;
  onOpenSettings: (_table: string) => void;
  onOpenRowFilter: (_table: string) => void;
  onOpenTransform: (_table: string) => void;
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const { entityName, tableItem } = entity;
  const isSelected = selectedTables.includes(tableItem.table);
  const isLocked = isSaving || isAssigningTables;
  const hasRowFilter = hasRowFilterUtil(tableItem);
  const transformBadge = tableItem.transform_summary?.badge;
  const hasTransform = Boolean(
    tableItem.transform_summary?.has_mapping ||
      tableItem.transform_summary?.has_script,
  );
  const destinationTableName =
    tableItem.transform_summary?.destination_table_name || null;
  const showEntityActions = isSelected || selectionLocked;

  const handleEntityCheckedChange = (checked: boolean) => {
    if (selectionLocked) return;
    setSelectedTables((prev) => {
      if (checked) {
        return prev.includes(tableItem.table)
          ? prev
          : [...prev, tableItem.table];
      } else {
        return prev.filter((t) => t !== tableItem.table);
      }
    });
    setHasChanged(true);
  };

  return (
    <Box
      borderBottom="1px solid"
      borderColor="gray.100"
      py={2}
      pl={4}
      bg={batchName ? "brand.50" : undefined}
      borderRadius={batchName ? "md" : undefined}
    >
      <Flex align="center" justify="space-between" gap={2}>
        <Flex align="center" gap={2}>
          <Box onClick={() => setIsOpen(!isOpen)} cursor="pointer" p={1}>
            {isOpen ? <IoCaretDownSharp size={12} /> : <IoMdPlay size={12} />}
          </Box>
          <Flex direction="column" minW={0}>
            <Text
              fontSize="sm"
              fontWeight="medium"
              color="gray.800"
              cursor="pointer"
              onClick={() => setIsOpen(!isOpen)}
            >
              {entityName}
            </Text>
            {destinationTableName && (
              <Tooltip
                content={`Snowflake destination: ${destinationTableName}`}
              >
                <Text fontSize="xs" color="gray.500" truncate>
                  {destinationTableName}
                </Text>
              </Tooltip>
            )}
          </Flex>
          {transformBadge && (
            <Badge size="sm" colorPalette="brand" variant="subtle">
              {transformBadge}
            </Badge>
          )}
        </Flex>

        <Flex gap={2} alignItems="center" mr={2}>
          {showEntityActions && (
            <>
              <Tooltip content="Mapping and transform">
                <IconButton
                  size="xs"
                  variant={hasTransform ? "subtle" : "ghost"}
                  colorPalette="brand"
                  color={hasTransform ? undefined : "gray.400"}
                  disabled={isLocked}
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenTransform(tableItem.table);
                  }}
                  aria-label="Mapping and transform"
                >
                  <TbTransform />
                </IconButton>
              </Tooltip>
              <IconButton
                size="xs"
                variant={hasRowFilter ? "subtle" : "ghost"}
                colorPalette="brand"
                disabled={isLocked}
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenRowFilter(tableItem.table);
                }}
                aria-label="Row filter"
              >
                <IoMdOptions />
              </IconButton>
              <IconButton
                size="xs"
                variant="ghost"
                colorPalette="brand"
                disabled={isLocked}
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenSettings(tableItem.table);
                }}
                aria-label="Target settings"
              >
                <IoMdSettings />
              </IconButton>
            </>
          )}
          <Flex justify="center" minW="40px">
            {tableItem.is_delta && (
              <TbDelta color="#2563EB" size={18} title="Delta table" />
            )}
          </Flex>
          <Flex justify="center" minW="40px">
            <Tooltip
              content={
                selectionLocked
                  ? `Assigned to batch "${batchName}" — remove from batch to deselect`
                  : undefined
              }
              disabled={!selectionLocked}
            >
              <Box>
                <Checkbox.Root
                  colorPalette="brand"
                  variant="solid"
                  disabled={selectionLocked}
                  checked={isSelected || selectionLocked}
                  onCheckedChange={({ checked }) =>
                    handleEntityCheckedChange(checked === true)
                  }
                >
                  <Checkbox.HiddenInput />
                  <Checkbox.Control
                    cursor={selectionLocked ? "not-allowed" : "pointer"}
                  />
                </Checkbox.Root>
              </Box>
            </Tooltip>
          </Flex>
        </Flex>
      </Flex>
      {isOpen && (
        <ColumnList
          tableName={tableItem.table}
          connectionId={connectionId}
          userSelectedFields={userSelectedFields}
          setUserSelectedFields={setUserSelectedFields}
          setHasChanged={setHasChanged}
          setSelectedTables={setSelectedTables}
        />
      )}
    </Box>
  );
};

const ServiceAccordion = ({
  serviceGroup,
  connectionId,
  selectedTables,
  setSelectedTables,
  userSelectedFields,
  setUserSelectedFields,
  setHasChanged,
  tableToBatchName,
  isSaving,
  isAssigningTables,
  onOpenSettings,
  onOpenRowFilter,
  onOpenTransform,
}: {
  serviceGroup: ServiceGroup;
  connectionId: number;
  selectedTables: string[];
  setSelectedTables: React.Dispatch<React.SetStateAction<string[]>>;
  userSelectedFields: Record<string, string[]>;
  setUserSelectedFields: React.Dispatch<
    React.SetStateAction<Record<string, string[]>>
  >;
  setHasChanged: React.Dispatch<React.SetStateAction<boolean>>;
  tableToBatchName: Map<string, string>;
  isSaving: boolean;
  isAssigningTables: boolean;
  onOpenSettings: (_table: string) => void;
  onOpenRowFilter: (_table: string) => void;
  onOpenTransform: (_table: string) => void;
}) => {
  const [isOpen, setIsOpen] = useState(true);
  const { serviceName, entities } = serviceGroup;

  const selectedEntities = entities.filter((e) =>
    selectedTables.includes(e.tableItem.table),
  );

  return (
    <Box borderBottom="1px solid" borderColor="gray.200" mb={1} pb={1}>
      <Flex align="center" justify="space-between" py={2} px={1}>
        <Flex align="center" gap={3}>
          <Box onClick={() => setIsOpen(!isOpen)} cursor="pointer" p={1}>
            {isOpen ? <IoCaretDownSharp /> : <IoMdPlay />}
          </Box>
          <Text
            fontSize="sm"
            color="gray.800"
            cursor="pointer"
            onClick={() => setIsOpen(!isOpen)}
          >
            {serviceName}
          </Text>
        </Flex>
        <Text fontSize="xs" fontWeight="semibold" color="gray.600" mr={4}>
          {selectedEntities.length}/{entities.length} table selected
        </Text>
      </Flex>
      {isOpen && (
        <VStack align="stretch" gap={0} pl={4}>
          {entities.map((entity) => {
            const batchName = tableToBatchName.get(
              entity.tableItem.table.toLowerCase(),
            );
            return (
              <EntityAccordion
                key={entity.tableItem.table}
                entity={entity}
                connectionId={connectionId}
                selectedTables={selectedTables}
                setSelectedTables={setSelectedTables}
                userSelectedFields={userSelectedFields}
                setUserSelectedFields={setUserSelectedFields}
                setHasChanged={setHasChanged}
                batchName={batchName}
                selectionLocked={!!batchName}
                isSaving={isSaving}
                isAssigningTables={isAssigningTables}
                onOpenSettings={onOpenSettings}
                onOpenRowFilter={onOpenRowFilter}
                onOpenTransform={onOpenTransform}
              />
            );
          })}
        </VStack>
      )}
    </Box>
  );
};

const SAPODataSchema = () => {
  const context = useOutletContext<Connector>();
  const [searchQuery, setSearchQuery] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [hasChanged, setHasChanged] = useState(false);
  const [shouldShowDisabledState, setShouldShowDisabledState] = useState(false);

  const {
    data: allTableData,
    isLoading: isAllTableListLoading,
    isError: isAllTableListError,
    error: allTableListError,
  } = useFetchConnectorTableById(context.connection_id);

  const AllTableList = allTableData?.tables;

  const { data: batchesData } = useFetchBatches(context.connection_id);

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

  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [userSelectedFields, setUserSelectedFields] = useState<
    Record<string, string[]>
  >({});

  const { data: tableStatusData } = useFetchTableStatus(
    context.connection_id,
    true,
  );

  const reloadingTables = useMemo(
    () => context.reloadingTables ?? [],
    [context.reloadingTables],
  );

  const [isAddServiceOpen, setIsAddServiceOpen] = useState(false);
  const [newServiceSearchQuery, setNewServiceSearchQuery] = useState("");
  const [searchedService, setSearchedService] = useState<string | null>(null);
  const [searchedEntities, setSearchedEntities] = useState<
    Array<{ entity_name: string; is_delta: boolean }>
  >([]);
  const [isSearchLoading, setIsSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selectedEntitiesMap, setSelectedEntitiesMap] = useState<
    Record<string, string[]>
  >({});
  const [isSubmittingEntities, setIsSubmittingEntities] = useState(false);

  const [activeTableForSettings, setActiveTableForSettings] = useState<
    string | null
  >(null);
  const [isSettingsModalOpen, setIsSettingsModalOpen] = useState(false);
  const [isUpdatingSettings, setIsUpdatingSettings] = useState(false);

  const [activeTableForFilter, setActiveTableForFilter] = useState<
    string | null
  >(null);
  const [isFilterModalOpen, setIsFilterModalOpen] = useState(false);
  const [isSavingFilter, setIsSavingFilter] = useState(false);

  const [activeTableForTransform, setActiveTableForTransform] = useState<
    string | null
  >(null);
  const [isTransformModalOpen, setIsTransformModalOpen] = useState(false);

  const activeFilterTableItem = useMemo(() => {
    return AllTableList?.find((t) => t.table === activeTableForFilter);
  }, [AllTableList, activeTableForFilter]);

  const activeTableItem = useMemo(() => {
    return AllTableList?.find((t) => t.table === activeTableForSettings);
  }, [AllTableList, activeTableForSettings]);

  const activeTransformTableItem = useMemo(() => {
    return AllTableList?.find((t) => t.table === activeTableForTransform);
  }, [AllTableList, activeTableForTransform]);

  const transformBadges = useMemo(() => {
    const map = new Map<string, string>();
    AllTableList?.forEach((t) => {
      const badge = t.transform_summary?.badge;
      if (badge) map.set(t.table.toLowerCase(), badge);
    });
    return map;
  }, [AllTableList]);

  const destinationByTable = useMemo(() => {
    const map = new Map<string, string>();
    AllTableList?.forEach((t) => {
      const dest = t.transform_summary?.destination_table_name;
      if (dest) map.set(t.table.toLowerCase(), dest);
    });
    return map;
  }, [AllTableList]);

  const occupiedDestinations = useMemo(() => {
    const map = new Map<string, string>();
    AllTableList?.forEach((t) => {
      if (t.table === activeTableForTransform) return;
      const dest = t.transform_summary?.destination_table_name?.toUpperCase();
      if (!dest) return;
      const service = t.service_name || "";
      map.set(dest, service ? `${service}_${t.table}` : t.table);
    });
    return map;
  }, [AllTableList, activeTableForTransform]);

  const activeTableSettings = useMemo(() => {
    if (!activeTableItem) return null;
    const serviceName =
      activeTableItem.service_name || activeTableItem.table.split("/")[0];
    const entityName = activeTableItem.service_name
      ? activeTableItem.table
      : activeTableItem.table.split("/")[1] ||
        activeTableItem.table.split("/")[0];
    const suggestedName = `${serviceName}_${entityName}`;

    return {
      output_file_name: activeTableItem.output_file_name || suggestedName,
      load_method: activeTableItem.load_method || "initial",
      partition_delta_by_date: activeTableItem.partition_delta_by_date || false,
      file_format: activeTableItem.file_format || "parquet",
      compression_method: activeTableItem.compression_method || "none",
      delete_and_load: !!activeTableItem.delete_and_load,
    };
  }, [activeTableItem]);

  const activeTableIsDelta = activeTableItem?.is_delta || false;

  const activeTableStatus = useMemo(() => {
    if (!activeTableForSettings || !tableStatusData?.tables) return null;
    return (
      tableStatusData.tables.find((t) => t.table === activeTableForSettings)
        ?.status || null
    );
  }, [activeTableForSettings, tableStatusData]);

  const activeTableDisplayName = useMemo(() => {
    if (!activeTableItem) return "";
    const serviceName =
      activeTableItem.service_name || activeTableItem.table.split("/")[0];
    const entityName = activeTableItem.service_name
      ? activeTableItem.table
      : activeTableItem.table.split("/")[1] ||
        activeTableItem.table.split("/")[0];
    return `${serviceName}_${entityName}`;
  }, [activeTableItem]);

  const activeTransformDisplayName = useMemo(() => {
    if (!activeTransformTableItem) return "";
    const serviceName =
      activeTransformTableItem.service_name ||
      activeTransformTableItem.table.split("/")[0];
    const entityName = activeTransformTableItem.service_name
      ? activeTransformTableItem.table
      : activeTransformTableItem.table.split("/")[1] ||
        activeTransformTableItem.table.split("/")[0];
    return `${serviceName}_${entityName}`;
  }, [activeTransformTableItem]);

  const handleSaveTargetSettings = async (settings: {
    output_file_name: string;
    load_method: string;
    partition_delta_by_date: boolean;
    file_format: string;
    compression_method: string;
    delete_and_load?: boolean;
    row_filter_config?: RowFilterConfig | null;
  }) => {
    if (!activeTableForSettings) return;
    setIsUpdatingSettings(true);
    try {
      await AxiosInstance.patch(
        ServerRoutes.connector.updateTableExportSettings(
          context.connection_id,
          activeTableForSettings,
        ),
        settings,
      );
      toaster.success({
        title: "Settings saved successfully",
      });

      const isFilterCleared = settings.row_filter_config === null;

      // Optimistically update the UI cache
      queryClient.setQueryData(
        ["ConnectorTable", context.connection_id],
        (oldData: ConnectorTablesResponse | undefined) => {
          if (!oldData || !oldData.tables) return oldData;
          return {
            ...oldData,
            tables: oldData.tables.map((t: ConnectorTable) => {
              if (t.table === activeTableForSettings) {
                return {
                  ...t,
                  ...settings,
                  ...(isFilterCleared
                    ? { row_filter_config: null, row_filter: null }
                    : {}),
                };
              }
              return t;
            }),
          };
        },
      );

      // Optimistically update tableFields cache if filter was cleared
      if (isFilterCleared) {
        queryClient.setQueryData(
          ["tableFields", context.connection_id, activeTableForSettings],
          (oldData: Record<string, unknown> | undefined) => {
            if (!oldData) return oldData;
            return {
              ...oldData,
              row_filter_config: null,
              row_filter: null,
            };
          },
        );
      }

      // Trigger background refetches
      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", context.connection_id],
      });
      queryClient.invalidateQueries({
        queryKey: [
          "tableFields",
          context.connection_id,
          activeTableForSettings,
        ],
      });
      setIsSettingsModalOpen(false);
      setActiveTableForSettings(null);
    } catch (err: unknown) {
      const errMsg = getErrorMessage(err, "Failed to save target settings");
      toaster.error({
        title: "Failed to save settings",
        description: errMsg,
      });
    } finally {
      setIsUpdatingSettings(false);
    }
  };

  const handleSaveRowFilter = async (
    config: RowFilterConfig | null,
    targetTableOverride?: string,
  ) => {
    const targetTable = targetTableOverride || activeTableForFilter;
    if (!targetTable) return;
    setIsSavingFilter(true);
    try {
      await AxiosInstance.patch(
        ServerRoutes.connector.updateTableExportSettings(
          context.connection_id,
          targetTable,
        ),
        {
          row_filter_config: config,
        },
      );

      // Optimistically update the UI cache so the filter icon highlights immediately
      queryClient.setQueryData(
        ["ConnectorTable", context.connection_id],
        (oldData: ConnectorTablesResponse | undefined) => {
          if (!oldData || !oldData.tables) return oldData;
          return {
            ...oldData,
            tables: oldData.tables.map((t: ConnectorTable) => {
              if (t.table === targetTable) {
                return {
                  ...t,
                  row_filter_config: config,
                };
              }
              return t;
            }),
          };
        },
      );

      // Optimistically update the tableFields cache for this table
      queryClient.setQueryData(
        ["tableFields", context.connection_id, targetTable],
        (oldData: Record<string, unknown> | undefined) => {
          if (!oldData) return oldData;
          return {
            ...oldData,
            row_filter_config: config,
            row_filter: config,
          };
        },
      );

      toaster.success({
        title: "Filter saved successfully",
      });
      // Trigger background refetch for both ConnectorTable and tableFields
      queryClient.invalidateQueries({
        queryKey: ["tableFields", context.connection_id, targetTable],
      });
      queryClient.invalidateQueries({
        queryKey: ["ConnectorTable", context.connection_id],
      });
    } catch (err: unknown) {
      const errMsg = getErrorMessage(err, "Failed to save filter settings");
      toaster.error({
        title: "Failed to save filter",
        description: errMsg,
      });
    } finally {
      setIsSavingFilter(false);
    }
  };

  useEffect(() => {
    if (!isAddServiceOpen) {
      setNewServiceSearchQuery("");
      setSearchedService(null);
      setSearchedEntities([]);
      setIsSearchLoading(false);
      setSearchError(null);
      setSelectedEntitiesMap({});
      setIsSubmittingEntities(false);
    }
  }, [isAddServiceOpen]);

  const handleSearchSubmit = async () => {
    const query = newServiceSearchQuery.trim();
    if (!query) return;

    setIsSearchLoading(true);
    setSearchError(null);
    setSearchedService(null);
    setSearchedEntities([]);

    try {
      const { data } = await AxiosInstance.get("/sap-odata/service-entities/", {
        params: {
          connection_id: context.connection_id,
          service_name: query,
        },
      });

      if (data && data.length > 0) {
        setSearchedService(query);
        setSearchedEntities(data);

        // Pre-populate checkboxes for entities that are already selected in the main table list
        const alreadySelected = AllTableList
          ? AllTableList.filter((t) => {
              const sName = t.service_name || t.table.split("/")[0];
              return (
                (sName || "").toLowerCase() === query.toLowerCase() &&
                t.selected
              );
            }).map((t) => {
              return t.service_name
                ? t.table
                : t.table.split("/")[1] || t.table.split("/")[0];
            })
          : [];

        setSelectedEntitiesMap((prev) => ({
          ...prev,
          [query]: alreadySelected,
        }));
      } else {
        setSearchError(`No services found matching "${query}"`);
      }
    } catch (err: unknown) {
      const errorMsg = getErrorMessage(err, "Failed to load entities");
      setSearchError(errorMsg);
    } finally {
      setIsSearchLoading(false);
    }
  };

  const handleSelectNewServicesSubmit = async () => {
    setIsSubmittingEntities(true);
    try {
      const savePromises = Object.entries(selectedEntitiesMap)
        .filter(([_, entities]) => entities.length > 0)
        .map(([serviceName, entities]) =>
          AxiosInstance.post("/sap-odata/save-entities/", {
            connection_id: context.connection_id,
            service_name: serviceName,
            entities: entities,
          }),
        );

      await Promise.all(savePromises);

      toaster.success({
        title: "Services selected",
        description: "Successfully added and registered new SAP services.",
      });

      // Refetch connection tables list
      await queryClient.refetchQueries({
        queryKey: ["ConnectorTable", context.connection_id],
      });

      setIsAddServiceOpen(false);
    } catch (err: unknown) {
      const errMsg = getErrorMessage(err, "Failed to select services");
      toaster.error({
        title: "Failed to save selected services",
        description: errMsg,
      });
    } finally {
      setIsSubmittingEntities(false);
    }
  };

  useEffect(() => {
    if (AllTableList) {
      const selected = AllTableList.filter(
        (t) =>
          isConnectorTableMarkedSelected(t.selected) ||
          tableToBatchName.has(t.table.toLowerCase()),
      )
        .sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
        .map((t) => t.table);
      setSelectedTables(selected);

      const fields: Record<string, string[]> = {};
      AllTableList.forEach((item) => {
        fields[item.table] = item.selected_fields ?? [];
      });
      setUserSelectedFields(fields);
      setHasChanged(false);
    }
  }, [AllTableList, tableToBatchName]);

  const pendingUnassignedTables = useMemo<UnassignedTable[]>(() => {
    if (!AllTableList) return [];
    const tableByKey = new Map(
      AllTableList.map((t) => [t.table.toLowerCase(), t]),
    );
    return selectedTables
      .filter((tableName) => !tableToBatchName.has(tableName.toLowerCase()))
      .map((tableName) => {
        const item = tableByKey.get(tableName.toLowerCase());
        return {
          table_name: tableName,
          sequence: item?.sequence ?? 0,
          last_synced: item?.last_synced ?? null,
        };
      });
  }, [AllTableList, selectedTables, tableToBatchName]);

  const groupedServices = useMemo(() => {
    const groups: Record<string, ServiceGroup> = {};

    AllTableList?.forEach((item) => {
      const serviceName = item.service_name || item.table.split("/")[0];
      const entityName = item.service_name
        ? item.table
        : item.table.split("/")[1] || item.table.split("/")[0];

      if (searchQuery) {
        const matchService = serviceName.toLowerCase().includes(searchQuery);
        const matchEntity = entityName.toLowerCase().includes(searchQuery);
        if (!matchService && !matchEntity) return;
      }

      if (!groups[serviceName]) {
        groups[serviceName] = {
          serviceName,
          entities: [],
        };
      }
      groups[serviceName].entities.push({
        entityName,
        tableItem: item,
      });
    });

    // Sort entities inside each service alphabetically
    Object.values(groups).forEach((group) => {
      group.entities.sort((a, b) => a.entityName.localeCompare(b.entityName));
    });

    // Sort services alphabetically by serviceName
    return Object.values(groups).sort((a, b) =>
      a.serviceName.localeCompare(b.serviceName),
    );
  }, [AllTableList, searchQuery]);

  const { mutateAsync: updateTablesAsync, isPending: isAssigningTables } =
    useUpdateSelectedTables({
      connectorId: context.connection_id,
    });

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await updateTablesAsync({ selected_tables: selectedTables });

      const updateFieldPromises = selectedTables.map((table) => {
        const cols = userSelectedFields[table] || [];
        return AxiosInstance.patch(
          ServerRoutes.connector.updateSelectedFields(
            context.connection_id,
            table,
          ),
          { selected_fields: cols },
        );
      });
      await Promise.all(updateFieldPromises);

      toaster.success({
        title: "Schema updated successfully",
        description:
          "Your service, entity, and column selections have been saved.",
      });
      setHasChanged(false);

      await queryClient.refetchQueries({
        queryKey: ["ConnectorTable", context.connection_id],
      });
      await queryClient.refetchQueries({
        queryKey: ["Batches", context.connection_id],
      });
    } catch (err: unknown) {
      const errorMsg = getErrorMessage(err, "Failed to save schema settings");
      toaster.error({
        title: "Failed to save schema settings",
        description: errorMsg,
      });
    } finally {
      setIsSaving(false);
    }
  };

  if (isAllTableListLoading) {
    return <LoadingSpinner />;
  }

  return (
    <Flex flexDirection="column" gap={4} pb={8} w="100%">
      <Actions
        shouldShowDisabledState={shouldShowDisabledState}
        setShouldShowDisabledState={setShouldShowDisabledState}
        reloadingTables={reloadingTables}
        onUpdateSchemaComplete={() => {
          queryClient.refetchQueries({
            queryKey: ["ConnectorTable", context.connection_id],
          });
        }}
        updateSchemaLabel="Add New Service"
        updateSchemaIcon={<GoPlus />}
        onUpdateSchemaClick={() => setIsAddServiceOpen(true)}
        updateSchemaPosition="left"
        leftChild={
          <InputGroup endElement={<MdSearch size={24} />} w="320px">
            <Input
              placeholder="Search service or entity"
              size="md"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value.toLowerCase())}
            />
          </InputGroup>
        }
      />

      <Grid templateColumns="1fr 1fr" gap={4}>
        <Flex
          direction="column"
          gap={2}
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="lg"
          padding={4}
          bgColor="white"
        >
          <Flex mb={4} justifyContent="space-between" alignItems="center">
            <Text fontSize="sm" fontWeight="semibold">
              Services
            </Text>
            <Flex gap={6} alignItems="center" mr={4}>
              <Text
                fontSize="sm"
                fontWeight="semibold"
                minW="40px"
                textAlign="center"
              >
                Delta
              </Text>
              <Text
                fontSize="sm"
                fontWeight="semibold"
                minW="40px"
                textAlign="center"
              >
                Select
              </Text>
            </Flex>
          </Flex>

          {!groupedServices.length && !isAllTableListLoading && (
            <Flex direction="column" alignItems="center" py={8}>
              <Text color={isAllTableListError ? "red.500" : "gray.500"}>
                {isAllTableListError
                  ? getErrorMessage(
                      allTableListError,
                      "Could not load schema. Try refresh.",
                    )
                  : "No Services available"}
              </Text>
            </Flex>
          )}

          {groupedServices.map((serviceGroup) => (
            <ServiceAccordion
              key={serviceGroup.serviceName}
              serviceGroup={serviceGroup}
              connectionId={context.connection_id}
              selectedTables={selectedTables}
              setSelectedTables={setSelectedTables}
              userSelectedFields={userSelectedFields}
              setUserSelectedFields={setUserSelectedFields}
              setHasChanged={setHasChanged}
              tableToBatchName={tableToBatchName}
              isSaving={isSaving}
              isAssigningTables={isAssigningTables}
              onOpenSettings={(table) => {
                setActiveTableForSettings(table);
                setIsSettingsModalOpen(true);
              }}
              onOpenRowFilter={(table) => {
                setActiveTableForFilter(table);
                setIsFilterModalOpen(true);
              }}
              onOpenTransform={(table) => {
                setActiveTableForTransform(table);
                setIsTransformModalOpen(true);
              }}
            />
          ))}
        </Flex>

        <BatchGroupedPanel
          connectionId={context.connection_id}
          pendingUnassignedTables={pendingUnassignedTables}
          transformBadges={transformBadges}
          sourceToDestination={destinationByTable}
        />
      </Grid>

      <ActionBar.Root open={hasChanged}>
        <Portal>
          <ActionBar.Positioner>
            <ActionBar.Content>
              <Button
                variant="solid"
                colorPalette="purple"
                size="sm"
                onClick={() => handleSave()}
                loading={isSaving || isAssigningTables}
              >
                <GoPlus />
                Save Selection
              </Button>
            </ActionBar.Content>
          </ActionBar.Positioner>
        </Portal>
      </ActionBar.Root>

      {/* "Select new services to sync" Modal/Dialog */}
      <Dialog.Root
        lazyMount
        open={isAddServiceOpen}
        onOpenChange={(details) => setIsAddServiceOpen(details.open)}
        size="lg"
      >
        <Portal>
          <Dialog.Backdrop />
          <Dialog.Positioner>
            <Dialog.Content>
              <Dialog.Header>
                <Dialog.Title fontSize="xl" fontWeight="bold">
                  Select new services to sync
                </Dialog.Title>
              </Dialog.Header>

              <Dialog.Body pb={6}>
                <VStack align="stretch" gap={4}>
                  <Text fontSize="sm" color="gray.600">
                    Search for one service at a time by entering its full name.
                  </Text>

                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      handleSearchSubmit();
                    }}
                    style={{ width: "100%" }}
                  >
                    <InputGroup startElement={<MdSearch size={20} />} w="100%">
                      <Input
                        placeholder="Search by service name"
                        size="md"
                        value={newServiceSearchQuery}
                        onChange={(e) =>
                          setNewServiceSearchQuery(e.target.value)
                        }
                      />
                    </InputGroup>
                  </form>

                  <Box
                    minH="300px"
                    maxH="400px"
                    overflowY="auto"
                    display="flex"
                    flexDirection="column"
                    justifyContent={
                      isSearchLoading || !searchedService || searchError
                        ? "center"
                        : "flex-start"
                    }
                    alignItems={
                      isSearchLoading || !searchedService || searchError
                        ? "center"
                        : "stretch"
                    }
                    py={2}
                  >
                    {isSearchLoading ? (
                      <LoadingSpinner />
                    ) : searchError ? (
                      <Text color="gray.500" fontSize="sm" textAlign="center">
                        {searchError}
                      </Text>
                    ) : !searchedService ? (
                      <Text color="gray.500" fontSize="sm" textAlign="center">
                        Enter full service name and press Enter to search
                      </Text>
                    ) : (
                      <VStack align="stretch" gap={3}>
                        {searchedEntities && searchedEntities.length > 0 && (
                          <Text fontSize="xs" color="gray.500" mb={1}>
                            {searchedEntities.length} matching result
                            {searchedEntities.length !== 1 ? "s" : ""}
                          </Text>
                        )}
                        <Box
                          borderBottom="1px solid"
                          borderColor="gray.100"
                          py={2}
                        >
                          <Flex align="center" gap={2} cursor="pointer">
                            <Box p={1}>
                              <IoCaretDownSharp size={12} />
                            </Box>
                            <Text
                              fontSize="sm"
                              fontWeight="semibold"
                              color="gray.800"
                            >
                              {searchedService}
                            </Text>
                          </Flex>

                          <Box pl={6} mt={2}>
                            <VStack align="stretch" gap={2}>
                              {/* Select All row */}
                              <Flex
                                align="center"
                                gap={3}
                                p={1}
                                borderRadius="md"
                                _hover={{ bg: "gray.50" }}
                              >
                                <Checkbox.Root
                                  colorPalette="brand"
                                  variant="solid"
                                  checked={
                                    searchedEntities &&
                                    (selectedEntitiesMap[searchedService] || [])
                                      .length === searchedEntities.length
                                      ? true
                                      : searchedEntities &&
                                          (
                                            selectedEntitiesMap[
                                              searchedService
                                            ] || []
                                          ).length > 0
                                        ? "indeterminate"
                                        : false
                                  }
                                  onCheckedChange={({ checked }) => {
                                    if (checked === true) {
                                      setSelectedEntitiesMap((prev) => ({
                                        ...prev,
                                        [searchedService]: searchedEntities.map(
                                          (e) => e.entity_name,
                                        ),
                                      }));
                                    } else {
                                      setSelectedEntitiesMap((prev) => ({
                                        ...prev,
                                        [searchedService]: [],
                                      }));
                                    }
                                  }}
                                >
                                  <Checkbox.HiddenInput />
                                  <Checkbox.Control cursor="pointer" />
                                  <Text
                                    fontSize="xs"
                                    fontWeight="semibold"
                                    color="gray.600"
                                  >
                                    {
                                      (
                                        selectedEntitiesMap[searchedService] ||
                                        []
                                      ).length
                                    }
                                    /{searchedEntities?.length || 0} tables
                                    selected
                                  </Text>
                                </Checkbox.Root>
                              </Flex>

                              {/* Entity rows */}
                              {searchedEntities.map((entity) => {
                                const isEntityChecked = (
                                  selectedEntitiesMap[searchedService] || []
                                ).includes(entity.entity_name);
                                return (
                                  <Flex
                                    key={entity.entity_name}
                                    align="center"
                                    gap={3}
                                    p={1}
                                    pl={4}
                                    borderRadius="md"
                                    _hover={{ bg: "gray.50" }}
                                  >
                                    <Checkbox.Root
                                      colorPalette="brand"
                                      variant="solid"
                                      checked={isEntityChecked}
                                      onCheckedChange={({ checked }) => {
                                        setSelectedEntitiesMap((prev) => {
                                          const current =
                                            prev[searchedService] || [];
                                          const next =
                                            checked === true
                                              ? [...current, entity.entity_name]
                                              : current.filter(
                                                  (e) =>
                                                    e !== entity.entity_name,
                                                );
                                          return {
                                            ...prev,
                                            [searchedService]: next,
                                          };
                                        });
                                      }}
                                    >
                                      <Checkbox.HiddenInput />
                                      <Checkbox.Control cursor="pointer" />
                                      <Text fontSize="sm" color="gray.700">
                                        {entity.entity_name}
                                      </Text>
                                    </Checkbox.Root>
                                  </Flex>
                                );
                              })}
                            </VStack>
                          </Box>
                        </Box>
                      </VStack>
                    )}
                  </Box>
                </VStack>
              </Dialog.Body>

              <Dialog.Footer
                justifyContent="flex-end"
                pt={4}
                borderTop="1px solid"
                borderTopColor="gray.100"
              >
                <Button
                  colorPalette="brand"
                  disabled={
                    Object.values(selectedEntitiesMap).flat().length === 0
                  }
                  loading={isSubmittingEntities}
                  onClick={handleSelectNewServicesSubmit}
                >
                  Select services
                </Button>
              </Dialog.Footer>

              <Dialog.CloseTrigger asChild>
                <CloseButton size="sm" />
              </Dialog.CloseTrigger>
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>

      {activeTableForSettings && activeTableSettings && (
        <TargetSettingsModal
          key={activeTableForSettings}
          open={isSettingsModalOpen}
          onClose={() => {
            setIsSettingsModalOpen(false);
            setActiveTableForSettings(null);
          }}
          tableName={activeTableForSettings}
          displayName={activeTableDisplayName}
          isDelta={activeTableIsDelta}
          status={activeTableStatus}
          connectionId={context.connection_id}
          settings={activeTableSettings}
          isSaving={isUpdatingSettings}
          onSave={handleSaveTargetSettings}
          loadMethodLocked={activeTableItem?.load_method_locked ?? false}
          initialCompletedFlag={
            activeTableItem?.initial_completed_flag ?? false
          }
          firstSyncTimestamp={activeTableItem?.first_sync_timestamp ?? null}
          destinationName={context.destination_name}
          isFileBasedDestination={context.is_file_based}
        />
      )}

      {isFilterModalOpen && activeTableForFilter && (
        <RowFilterModal
          open={isFilterModalOpen}
          onClose={() => {
            setIsFilterModalOpen(false);
            setActiveTableForFilter(null);
          }}
          tableName={activeTableForFilter}
          connectionId={context.connection_id}
          initialRowFilter={getEffectiveRowFilter(activeFilterTableItem)}
          loadMethod={activeFilterTableItem?.load_method || "initial"}
          isInitialSyncDone={!!activeFilterTableItem?.first_sync_timestamp}
          onSave={handleSaveRowFilter}
          isSaving={isSavingFilter}
        />
      )}

      {isTransformModalOpen && activeTransformTableItem && (
        <Suspense fallback={null}>
          <TransformModal
            open={isTransformModalOpen}
            onClose={() => {
              setIsTransformModalOpen(false);
              setActiveTableForTransform(null);
            }}
            connectionId={context.connection_id}
            tableItem={activeTransformTableItem}
            modalTitle={activeTransformDisplayName}
            destinationName={context.destination_name}
            occupiedDestinations={occupiedDestinations}
          />
        </Suspense>
      )}
    </Flex>
  );
};

export default SAPODataSchema;
