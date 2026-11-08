import { useEffect, useRef, useState } from "react";

import { Box, Flex, Text } from "@chakra-ui/react";

import { LuFilter } from "react-icons/lu";

import { createPortal } from "react-dom";

export type Column<T> = {
  header: string;
  accessor: keyof T | "actions";
  render?: (_value: unknown, _row: T) => React.ReactNode;
  textAlign?: "left" | "center" | "right";
  width?: string | number;
  filterValue?: string;
  filterOptions?: string[];
  onFilterChange?: (_value: string) => void;
};

type SupportListTableProps<T> = {
  data: T[];
  columns: Column<T>[];
  rowHeight?: number;
  onRowClick?: (_row: T) => void;
};

const BORDER = "1px solid #e2e8f0";
const HEADER_BG = "#f8f8f8";
const ROW_STRIPE = "#f8f8f8";

const FilterHeader = ({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (_v: string) => void;
}) => {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLDivElement>(null);

  const handleClick = () => {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setPos({
        top: rect.bottom + window.scrollY + 4, // below the trigger
        left: rect.left + window.scrollX,
      });
    }
    setOpen((o) => !o);
  };

  // close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <>
      <Flex
        ref={triggerRef}
        align="center"
        gap={1}
        cursor="pointer"
        display="inline-flex"
        onClick={handleClick}
      >
        <Text
          as="span"
          fontSize="xs"
          fontWeight="semibold"
          color={value !== "all" ? "#6e2fd5" : "gray.700"}
        >
          {label}
        </Text>
        <LuFilter size={11} color={value !== "all" ? "#6e2fd5" : "#718096"} />
      </Flex>

      {open &&
        createPortal(
          <Box
            position="absolute"
            top={`${pos.top}px`}
            left={`${pos.left}px`}
            bg="white"
            border={BORDER}
            borderRadius="md"
            boxShadow="lg"
            zIndex={9999}
            minW="150px"
            py={1}
          >
            {options.map((opt) => (
              <Box
                key={opt}
                px={3}
                py="5px"
                fontSize="xs"
                cursor="pointer"
                bg={value === opt ? "#f4e9ff" : "white"}
                fontWeight={value === opt ? "semibold" : "normal"}
                color={value === opt ? "#6e2fd5" : "#2d3748"}
                _hover={{ bg: "#f4e9ff" }}
                onMouseDown={() => {
                  onChange(opt);
                  setOpen(false);
                }}
              >
                {opt}
              </Box>
            ))}
          </Box>,
          document.body,
        )}
    </>
  );
};

const SupportListTable = <T,>({
  data,
  columns,
  rowHeight: initialRowHeight = 32,
  onRowClick,
}: SupportListTableProps<T>) => {
  const parseWidth = (w: string | number | undefined): number => {
    if (typeof w === "number") return w;
    if (typeof w === "string") {
      if (w.endsWith("%")) return Math.round((parseInt(w) / 100) * 900);
      return parseInt(w) || 120;
    }
    return 120;
  };

  const [colWidths, setColWidths] = useState<number[]>(() =>
    columns.map((c) => parseWidth(c.width)),
  );

  const startColResize = (e: React.MouseEvent, idx: number) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = colWidths[idx];
    const onMove = (ev: MouseEvent) => {
      const newW = Math.max(40, startW + ev.clientX - startX);
      setColWidths((prev) => {
        const next = [...prev];
        next[idx] = newW;
        return next;
      });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const totalWidth = colWidths.reduce((a, b) => a + b, 0);

  return (
    <Box
      border={BORDER}
      borderRadius="md"
      overflowX="auto"
      overflowY="auto"
      maxH="70vh"
      bg="white"
    >
      <table
        style={{
          borderCollapse: "collapse",
          width: `${totalWidth}px`,
          minWidth: "100%",
          tableLayout: "fixed",
        }}
      >
        <colgroup>
          {colWidths.map((w, i) => (
            <col key={i} style={{ width: `${w}px` }} />
          ))}
        </colgroup>

        <thead>
          <tr
            style={{ background: HEADER_BG, height: `${initialRowHeight}px` }}
          >
            {columns.map((col, idx) => (
              <th
                key={idx}
                style={{
                  borderRight:
                    idx === columns.length - 1 ? "none" : "1px solid white",
                  borderBottom: BORDER,
                  padding: "0 8px",
                  textAlign: col.textAlign || "left",
                  fontWeight: 600,
                  fontSize: "12px",
                  color: "#4a5568",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  userSelect: "none",
                  background: HEADER_BG,
                  position: "relative",
                }}
              >
                {col.filterOptions &&
                col.filterValue !== undefined &&
                col.onFilterChange ? (
                  <FilterHeader
                    label={col.header}
                    value={col.filterValue}
                    options={col.filterOptions}
                    onChange={col.onFilterChange}
                  />
                ) : (
                  <Text
                    as="span"
                    fontSize="xs"
                    fontWeight="semibold"
                    color="gray.700"
                  >
                    {col.header}
                  </Text>
                )}
                <Box
                  position="absolute"
                  right={0}
                  top={0}
                  h="100%"
                  w="4px"
                  cursor="col-resize"
                  zIndex={2}
                  onMouseDown={(e: React.MouseEvent) => startColResize(e, idx)}
                  _hover={{ bg: "#c69aff" }}
                />
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {data.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                style={{
                  textAlign: "center",
                  padding: "24px",
                  fontSize: "13px",
                  color: "#a0aec0",
                  border: BORDER,
                }}
              >
                No tickets found
              </td>
            </tr>
          ) : (
            data.map((item, rowIdx) => (
              <tr
                key={rowIdx}
                onClick={() => onRowClick?.(item)}
                style={{
                  height: `${initialRowHeight}px`,
                  background: (item as Record<string, unknown>).has_new_response
                    ? "#e6f6ff"
                    : rowIdx % 2 === 0
                      ? "white"
                      : ROW_STRIPE,
                  cursor: onRowClick ? "pointer" : "default",
                }}
                onMouseOver={(e) => {
                  if (onRowClick)
                    e.currentTarget.style.background = (
                      item as Record<string, unknown>
                    ).has_new_response
                      ? "#d0ebff"
                      : "#f4f4f4";
                }}
                onMouseOut={(e) => {
                  if (onRowClick)
                    e.currentTarget.style.background = (
                      item as Record<string, unknown>
                    ).has_new_response
                      ? "#e6f6ff"
                      : rowIdx % 2 === 0
                        ? "white"
                        : ROW_STRIPE;
                }}
              >
                {columns.map((col, colIdx) => (
                  <td
                    key={colIdx}
                    style={{
                      borderRight:
                        colIdx === columns.length - 1
                          ? "none"
                          : "1px solid white",
                      padding: "0 8px",
                      textAlign: col.textAlign || "left",
                      fontSize: "12px",
                      color: "#2d3748",
                      overflow: "hidden",
                      whiteSpace: "nowrap",
                      textOverflow: "ellipsis",
                      position: "relative",
                    }}
                  >
                    {col.render
                      ? col.render(item[col.accessor as keyof T], item)
                      : String(item[col.accessor as keyof T] ?? "-")}
                    <Box
                      position="absolute"
                      right={0}
                      top={0}
                      h="100%"
                      w="4px"
                      cursor="col-resize"
                      zIndex={2}
                      onMouseDown={(e: React.MouseEvent) =>
                        startColResize(e, colIdx)
                      }
                      _hover={{ bg: "#e1c7ff" }}
                    />
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </Box>
  );
};

export default SupportListTable;
