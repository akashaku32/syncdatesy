import { useEffect, useMemo, useState } from "react";

import { Box, Flex, Input, InputGroup, Spinner, Text } from "@chakra-ui/react";

import { IoMdPlay } from "react-icons/io";
import { IoCaretDownSharp } from "react-icons/io5";
import { MdSearch } from "react-icons/md";

import Pagination from "@/components/shared/Pagination";
import useFetchDestinationTableFields from "@/queryOptions/connector/reverseSchema/useFetchDestinationTableFields";
import { type ReverseSchemaResponse } from "@/queryOptions/connector/reverseSchema/useFetchReverseSchema";
import { usePagination } from "@/queryOptions/connector/schema/usePagination";
import { type ConnectorTable } from "@/types/connectors";

import { isPrimaryKey } from "../../utils/validation";

const ITEMS_PER_PAGE = 10;

interface DestinationTableRowProps {
  connectionId: number;
  item: ConnectorTable;
  index: number;
  expanded: boolean;
  dropTarget: string | null;
  onToggleExpand: (_table: string) => void;
  onDropTargetChange: (_table: string | null) => void;
  onClearDropTarget: (_table: string) => void;
  onDrop: (_sourceTable: string, _destinationTable: string) => void;
}

const DestinationTableRow = ({
  connectionId,
  item,
  index,
  expanded,
  dropTarget,
  onToggleExpand,
  onDropTargetChange,
  onClearDropTarget,
  onDrop,
}: DestinationTableRowProps) => {
  const { table, table_fields: inlineFields } = item;
  const isEven = index % 2 === 0;
  const rowBg = isEven ? "gray.100" : "white";
  const isExpanded = expanded;

  const { data: tableFieldsData, isLoading: isLoadingFields } =
    useFetchDestinationTableFields(connectionId, table, isExpanded);

  const fieldsToShow =
    tableFieldsData?.table_fields &&
    Object.keys(tableFieldsData.table_fields).length > 0
      ? tableFieldsData.table_fields
      : inlineFields;

  return (
    <Flex
      onDragEnter={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onDropTargetChange(table);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = "move";
        onDropTargetChange(table);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.currentTarget === e.target) {
          onClearDropTarget(table);
        }
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onDropTargetChange(null);

        let sourceTable: string | undefined =
          e.dataTransfer.getData("source-table") || undefined;

        if (!sourceTable) {
          sourceTable = e.dataTransfer.getData("text/plain") || undefined;
        }

        if (!sourceTable) {
          try {
            const jsonData = e.dataTransfer.getData("application/json");
            if (jsonData) {
              const parsed = JSON.parse(jsonData);
              sourceTable = parsed.sourceTable;
            }
          } catch {
            // Ignore
          }
        }

        if (!sourceTable) {
          sourceTable = (window as { __currentDragSource?: string })
            .__currentDragSource;
        }

        if (sourceTable && sourceTable.trim() !== "" && sourceTable !== table) {
          onDrop(sourceTable.trim(), table);
        }
      }}
      justifyContent="space-between"
      backgroundColor={dropTarget === table ? "green.50" : rowBg}
      alignItems="center"
      direction={isExpanded ? "column" : "row"}
      padding={2}
      borderRadius={4}
      minHeight="60px"
      width="100%"
      borderWidth={dropTarget === table ? 2 : 0}
      borderColor={dropTarget === table ? "green.400" : "transparent"}
      style={{
        position: "relative",
        transition: "all 0.2s ease",
      }}
      _hover={{
        backgroundColor: dropTarget === table ? "green.50" : "gray.50",
      }}
    >
      <Flex
        alignItems="center"
        justifyContent="space-between"
        gap={2}
        width="100%"
      >
        <Flex alignItems="center" gap={2} flex="1">
          <Box
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onToggleExpand(table);
            }}
            onMouseDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            style={{
              cursor: "pointer",
              position: "relative",
              zIndex: 1000,
              pointerEvents: "auto",
            }}
            padding={1}
            _hover={{
              backgroundColor: "brand.200",
              borderRadius: 4,
            }}
          >
            {isExpanded ? <IoCaretDownSharp /> : <IoMdPlay />}
          </Box>
          <Text
            fontSize="sm"
            fontWeight="medium"
            style={{
              cursor: "default",
              position: "relative",
              zIndex: 1,
            }}
          >
            {table}
          </Text>
        </Flex>
      </Flex>

      {isExpanded && (
        <Flex
          direction="column"
          gap={1.5}
          width="100%"
          onMouseDown={(e) => {
            e.stopPropagation();
          }}
          onClick={(e) => {
            e.stopPropagation();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          onDrop={(e) => {
            e.stopPropagation();
          }}
          mt={3}
          pt={3}
          pb={3}
          pl={4}
          pr={4}
          bgColor="white"
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="md"
          borderStyle="solid"
          boxShadow="sm"
          style={{ pointerEvents: "auto" }}
        >
          {isLoadingFields && !fieldsToShow ? (
            <Flex justifyContent="center" py={2}>
              <Spinner size="sm" />
            </Flex>
          ) : fieldsToShow && Object.keys(fieldsToShow).length > 0 ? (
            Object.entries(fieldsToShow).map(([field, fieldInfo]) => {
              const dataType =
                typeof fieldInfo === "string"
                  ? fieldInfo
                  : typeof fieldInfo === "object" && fieldInfo !== null
                    ? (fieldInfo as { data_type?: string }).data_type ||
                      "unknown"
                    : "unknown";
              const isPK = isPrimaryKey(field, fieldInfo);

              return (
                <Flex
                  key={field}
                  direction="column"
                  gap={1}
                  width="100%"
                  py={1}
                  px={2}
                  borderRadius="sm"
                  _hover={{
                    bgColor: "gray.50",
                  }}
                >
                  <Flex alignItems="center" gap={2}>
                    {isPK && (
                      <Text fontSize="sm" color="yellow.600">
                        🔑
                      </Text>
                    )}
                    <Text fontSize="sm" fontWeight="medium" color="gray.700">
                      {field}
                    </Text>
                    <Text fontSize="sm" color="gray.500">
                      :
                    </Text>
                    <Text fontSize="sm" color="gray.600" fontStyle="italic">
                      {dataType}
                    </Text>
                  </Flex>
                </Flex>
              );
            })
          ) : (
            <Text fontSize="sm" color="gray.500" fontStyle="italic">
              No fields available
            </Text>
          )}
        </Flex>
      )}
    </Flex>
  );
};

interface DestinationProps {
  connectionId: number;
  onDrop: (_sourceTable: string, _destinationTable: string) => void;
  reverseSchemaData: ReverseSchemaResponse | null;
}

const Destination = (props: DestinationProps) => {
  const { connectionId, onDrop, reverseSchemaData } = props;

  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const toggleExpand = (table: string) =>
    setExpanded((prev) => ({
      ...prev,
      [table]: !prev[table],
    }));

  const filteredTables = useMemo(() => {
    const destinationTableList = reverseSchemaData?.destination_tables || [];
    if (!destinationTableList.length) return [];
    return destinationTableList.filter((item) =>
      item.table.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }, [reverseSchemaData?.destination_tables, searchQuery]);

  const {
    currentData: paginatedTables,
    currentPage,
    totalPages,
    jumpToPage,
  } = usePagination({ data: filteredTables, itemsPerPage: ITEMS_PER_PAGE });

  useEffect(() => {
    jumpToPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery]);

  return (
    <Flex
      direction="column"
      gap={2}
      borderWidth={1}
      borderColor="gray.300"
      borderRadius="lg"
      padding={4}
      bgColor="white"
      w="100%"
      maxW="100%"
      overflow="hidden"
    >
      <Flex mb={4} direction="column" gap={1}>
        <Text fontSize="sm" fontWeight="semibold">
          Destination Tables
        </Text>
        <Text fontSize="xs" color="gray.500">
          Drop a source table here to create a mapping.
        </Text>
      </Flex>

      <Flex mb={4}>
        <InputGroup endElement={<MdSearch size={24} />}>
          <Input
            id="destination-table-search"
            name="destination-table-search"
            placeholder="Search table name"
            size="sm"
            onChange={(e) => {
              setSearchQuery(e.target.value);
            }}
          />
        </InputGroup>
      </Flex>

      {!filteredTables.length && (
        <Flex direction="column" alignItems="center" py={8}>
          <Text>No Destination Tables available</Text>
        </Flex>
      )}

      {filteredTables.length > 0 && (
        <>
          <Flex direction="column" gap={2}>
            {paginatedTables.map((item, index) => (
              <DestinationTableRow
                key={item.table}
                connectionId={connectionId}
                item={item}
                index={index}
                expanded={!!expanded[item.table]}
                dropTarget={dropTarget}
                onToggleExpand={toggleExpand}
                onDropTargetChange={setDropTarget}
                onClearDropTarget={(table) =>
                  setDropTarget((prev) => (prev === table ? null : prev))
                }
                onDrop={onDrop}
              />
            ))}
          </Flex>

          {totalPages > 1 && (
            <Pagination
              currentPage={currentPage}
              totalPages={totalPages}
              onPageChange={jumpToPage}
            />
          )}
        </>
      )}
    </Flex>
  );
};

export default Destination;
