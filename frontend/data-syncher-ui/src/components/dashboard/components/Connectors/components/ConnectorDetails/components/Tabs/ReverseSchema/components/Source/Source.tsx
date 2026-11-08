import { useEffect, useMemo, useState } from "react";

import { Box, Flex, Input, InputGroup, Spinner, Text } from "@chakra-ui/react";

import { IoMdPlay } from "react-icons/io";
import { IoCaretDownSharp } from "react-icons/io5";
import { MdSearch } from "react-icons/md";

import Pagination from "@/components/shared/Pagination";
import useFetchTableFields from "@/queryOptions/connector/schema/useFetchTableFields";
import { usePagination } from "@/queryOptions/connector/schema/usePagination";
import {
  type ConnectorTable,
  type ReverseSchemaResponse,
} from "@/types/connectors";

import { isPrimaryKey } from "../../utils/validation";

const ITEMS_PER_PAGE = 10;

interface SourceTableRowProps {
  connectionId: number;
  item: ConnectorTable;
  index: number;
  expanded: boolean;
  selectedTable: string | null;
  draggedTable: string | null;
  onToggleExpand: (_table: string) => void;
  onSelectTable: (_table: string | null) => void;
  onDragStart: (_table: string) => void;
  onDragEnd: () => void;
}

const SourceTableRow = ({
  connectionId,
  item,
  index,
  expanded,
  selectedTable,
  draggedTable,
  onToggleExpand,
  onSelectTable,
  onDragStart,
  onDragEnd,
}: SourceTableRowProps) => {
  const { table, table_fields: inlineFields } = item;
  const isEven = index % 2 === 0;
  const rowBg = isEven ? "gray.100" : "white";
  const isExpanded = expanded;
  const isSelected = selectedTable === table;
  const isDragging = draggedTable === table;

  const { data: tableFieldsData, isLoading: isLoadingFields } =
    useFetchTableFields(connectionId, table, isExpanded);

  const fieldsToShow =
    tableFieldsData?.table_fields &&
    Object.keys(tableFieldsData.table_fields).length > 0
      ? tableFieldsData.table_fields
      : inlineFields;

  return (
    <Flex
      draggable
      onClick={() => onSelectTable(isSelected ? null : table)}
      onDragStart={(e) => {
        onSelectTable(table);
        onDragStart(table);
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", table);
        e.dataTransfer.setData("source-table", table);
        (window as { __currentDragSource?: string }).__currentDragSource =
          table;
      }}
      onDragEnd={onDragEnd}
      justifyContent="space-between"
      backgroundColor={isDragging ? "blue.100" : isSelected ? "blue.50" : rowBg}
      alignItems="center"
      direction={isExpanded ? "column" : "row"}
      padding={2}
      borderRadius={4}
      title="Drag onto a destination table to create a mapping"
      style={{
        cursor: isDragging ? "grabbing" : "grab",
        userSelect: "none",
        WebkitUserSelect: "none",
        opacity: isDragging ? 0.8 : 1,
      }}
      borderWidth={isDragging || isSelected ? 2 : 0}
      borderColor={
        isDragging ? "blue.500" : isSelected ? "blue.300" : "transparent"
      }
      _hover={
        isDragging
          ? {}
          : {
              backgroundColor: isSelected ? "blue.100" : "gray.50",
            }
      }
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
              e.stopPropagation();
              onToggleExpand(table);
            }}
            cursor="pointer"
            padding={1}
            _hover={{
              backgroundColor: "brand.200",
              borderRadius: 4,
            }}
          >
            {isExpanded ? <IoCaretDownSharp /> : <IoMdPlay />}
          </Box>
          <Text fontSize="sm" fontWeight="medium" flex="1">
            {table}
          </Text>
        </Flex>
      </Flex>

      {isExpanded && (
        <Flex
          direction="column"
          gap={2}
          paddingBlock={4}
          width="100%"
          onClick={(e) => e.stopPropagation()}
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
                  : (fieldInfo as { data_type: string }).data_type;
              const isPK = isPrimaryKey(field, fieldInfo);

              return (
                <Flex key={field} direction="column" gap={1} width="100%">
                  <Flex alignItems="center" gap={2}>
                    {isPK && <Text>🔑</Text>}
                    <Text fontSize="sm">
                      {field}: {dataType}
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

interface SourceProps {
  connectionId: number;
  reverseSchemaData: ReverseSchemaResponse | null;
}

const Source = ({ connectionId, reverseSchemaData }: SourceProps) => {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [draggedTable, setDraggedTable] = useState<string | null>(null);

  const toggleExpand = (table: string) =>
    setExpanded((prev) => ({
      ...prev,
      [table]: !prev[table],
    }));

  const filteredTables = useMemo(() => {
    const sourceTableList = reverseSchemaData?.source_tables || [];
    if (!sourceTableList.length) return [];
    return sourceTableList.filter((item) =>
      item.table.toLowerCase().includes(searchQuery.toLowerCase()),
    );
  }, [reverseSchemaData?.source_tables, searchQuery]);

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
    >
      <Flex mb={4} direction="column" gap={1}>
        <Text fontSize="sm" fontWeight="semibold">
          Source Tables
        </Text>
        <Text fontSize="xs" color="gray.500">
          Drag a table onto a destination to map it.
        </Text>
      </Flex>

      <Flex mb={4}>
        <InputGroup endElement={<MdSearch size={24} />}>
          <Input
            placeholder="Search table name"
            size="sm"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </InputGroup>
      </Flex>

      {!filteredTables.length && (
        <Flex direction="column" alignItems="center" py={8}>
          <Text>No Source Tables available</Text>
        </Flex>
      )}

      {filteredTables.length > 0 && (
        <>
          <Flex direction="column" gap={2}>
            {paginatedTables.map((item, index) => (
              <SourceTableRow
                key={item.table}
                connectionId={connectionId}
                item={item}
                index={index}
                expanded={!!expanded[item.table]}
                selectedTable={selectedTable}
                draggedTable={draggedTable}
                onToggleExpand={toggleExpand}
                onSelectTable={setSelectedTable}
                onDragStart={setDraggedTable}
                onDragEnd={() => {
                  setDraggedTable(null);
                  setSelectedTable(null);
                  delete (window as { __currentDragSource?: string })
                    .__currentDragSource;
                }}
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

export default Source;
