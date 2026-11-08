import {
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  Badge,
  Box,
  Button,
  Flex,
  IconButton,
  NativeSelect,
  Text,
} from "@chakra-ui/react";

import { FiLock, FiX } from "react-icons/fi";
import { MdDragIndicator } from "react-icons/md";

import { type BinaryConfirmOptions } from "@/components/ui/confirm";
import { Tooltip } from "@/components/ui/tooltip";

import { confirmCopy } from "./confirmCopy";
import {
  type MappingRow,
  type SapColumn,
  formatFieldType,
  jsonSourceNames,
  targetNameError,
} from "./mappingState";
import { useVirtualizer } from "@tanstack/react-virtual";

type FilterChip =
  | "all"
  | "unmapped_source"
  | "unmapped_target"
  | "warnings"
  | "removed";

type ColumnMapperProps = {
  rows: MappingRow[];
  removedRows?: MappingRow[];
  sapColumns: SapColumn[];
  jsonRaw: Record<string, unknown> | null;
  search: string;
  filter: FilterChip;
  readOnly?: boolean;
  onToggleSource: (_name: string, _checked: boolean) => void;
  onToggleJsonSource: (_name: string, _checked: boolean) => void;
  onRepoint: (_targetId: string, _source: string) => void;
  onRename: (_targetId: string, _name: string) => void;
  onClear: (_targetId: string) => void;
  onAddTarget: (_source: string) => void;
  onSelectAll: (_names: string[]) => void;
  onSelectAllJson: () => void;
  onClearJson: () => void;
  onRestore: (_targetId: string) => void;
  onRestoreAll: () => void;
  onReorder: (_orderedIds: string[]) => void;
  onAnnounce: (_message: string) => void;
  confirm: (_options: BinaryConfirmOptions) => Promise<boolean>;
  paneMaxHeight?: string;
};

const WARNING = new Set(["Narrowed", "Date check", "Not in SAP"]);

const STATUS_PALETTE: Record<string, string> = {
  Direct: "green",
  Narrowed: "orange",
  "Date check": "orange",
  "Not in SAP": "red",
  "From script": "purple",
  Constant: "blue",
  "Added by user": "purple",
  Overridden: "purple",
  Dropped: "gray",
};

const thinScroll = {
  scrollbarWidth: "thin",
  overscrollBehavior: "contain",
  "&::-webkit-scrollbar": { width: "6px" },
  "&::-webkit-scrollbar-thumb": {
    background: "var(--chakra-colors-gray-300)",
    borderRadius: "999px",
  },
} as const;

const typeLabel = (column?: SapColumn) =>
  formatFieldType(column?.type, column?.length, column?.decimals);

const JSON_GROUP_HEADER = 48;

const ColumnMapper = ({
  rows,
  removedRows = [],
  sapColumns,
  jsonRaw,
  search,
  filter,
  readOnly,
  onToggleSource,
  onToggleJsonSource,
  onRepoint,
  onRename,
  onClear,
  onAddTarget,
  onSelectAll,
  onSelectAllJson,
  onClearJson,
  onRestore,
  onRestoreAll,
  onReorder,
  onAnnounce,
  confirm,
  paneMaxHeight = "420px",
}: ColumnMapperProps) => {
  const shellRef = useRef<HTMLDivElement>(null);
  const sourceRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef<HTMLDivElement>(null);
  const [hoverTargetId, setHoverTargetId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [dragSource, setDragSource] = useState<string | null>(null);
  const [dragFromTarget, setDragFromTarget] = useState<string | null>(null);
  const [reorderId, setReorderId] = useState<string | null>(null);
  const [floatLabel, setFloatLabel] = useState<string | null>(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const confirmRef = useRef(confirm);
  confirmRef.current = confirm;
  const dragMoved = useRef(false);
  const dragOrigin = useRef({ x: 0, y: 0 });
  const ghostRef = useRef<HTMLDivElement>(null);
  const dropTargetRef = useRef<string | null>(null);
  const targetRowsRef = useRef<MappingRow[]>([]);
  const onReorderRef = useRef(onReorder);
  onReorderRef.current = onReorder;
  const newTargetDropHandled = useRef(false);
  const suppressNewTargetClick = useRef(false);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const [showConnectors, setShowConnectors] = useState(false);
  const [connector, setConnector] = useState<{
    x1: number;
    y1: number;
    x2: number;
    y2: number;
  } | null>(null);
  const [editValue, setEditValue] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [removedOpen, setRemovedOpen] = useState(true);

  const jsonSources = useMemo(() => jsonSourceNames(jsonRaw), [jsonRaw]);
  const hasJson = Boolean(jsonRaw && Array.isArray(jsonRaw.field_mapping));
  const query = search.trim().toLowerCase();
  const columnByName = useMemo(
    () => new Map(sapColumns.map((column) => [column.name, column])),
    [sapColumns],
  );
  const targetTypeByName = useMemo(() => {
    const node = jsonRaw?.target;
    const fields =
      node &&
      typeof node === "object" &&
      Array.isArray((node as { fields?: unknown }).fields)
        ? (node as { fields: unknown[] }).fields
        : [];
    const byName = new Map<string, string>();
    fields.forEach((field) => {
      if (!field || typeof field !== "object") return;
      const meta = field as {
        fieldname?: string;
        datatype?: string;
        length?: number;
        decimals?: number;
      };
      if (!meta.fieldname) return;
      const label = formatFieldType(
        meta.datatype,
        Number(meta.length || 0) || undefined,
        Number(meta.decimals || 0) || undefined,
      );
      if (label) byName.set(String(meta.fieldname), label);
    });
    return byName;
  }, [jsonRaw]);

  const sourceRows = useMemo(() => {
    return sapColumns.filter((column) => {
      const mapped = rows.some((row) => row.source === column.name);
      if (filter === "unmapped_source" && mapped) return false;
      if (filter === "warnings") {
        const hit = rows.some(
          (row) => row.source === column.name && WARNING.has(row.status),
        );
        if (!hit) return false;
      }
      if (!query) return true;
      return column.name.toLowerCase().includes(query);
    });
  }, [filter, query, rows, sapColumns]);

  const fileColumns = useMemo(
    () =>
      hasJson
        ? sapColumns.filter((column) => jsonSources.has(column.name))
        : sapColumns,
    [hasJson, jsonSources, sapColumns],
  );
  const extraColumns = useMemo(
    () =>
      hasJson
        ? sapColumns.filter((column) => !jsonSources.has(column.name))
        : [],
    [hasJson, jsonSources, sapColumns],
  );

  const fromJson = useMemo(
    () =>
      hasJson
        ? sourceRows.filter((column) => jsonSources.has(column.name))
        : sourceRows,
    [hasJson, jsonSources, sourceRows],
  );
  const notInJson = useMemo(
    () =>
      hasJson
        ? sourceRows.filter((column) => !jsonSources.has(column.name))
        : [],
    [hasJson, jsonSources, sourceRows],
  );

  const targetRows = useMemo(() => {
    return rows.filter((row) => {
      if (filter === "removed") return false;
      if (filter === "unmapped_target" && row.source) return false;
      if (filter === "unmapped_source" && row.source) return false;
      if (filter === "warnings" && !WARNING.has(row.status)) return false;
      if (!query) return true;
      return (
        row.target.toLowerCase().includes(query) ||
        (row.source || "").toLowerCase().includes(query)
      );
    });
  }, [filter, query, rows]);
  targetRowsRef.current = targetRows;

  const fromVirtualizer = useVirtualizer({
    count: fromJson.length,
    getScrollElement: () => sourceRef.current,
    estimateSize: () => 34,
    scrollMargin: hasJson ? JSON_GROUP_HEADER : 0,
    overscan: 10,
  });
  const notOffset =
    (hasJson ? JSON_GROUP_HEADER : 0) +
    fromVirtualizer.getTotalSize() +
    (hasJson ? 32 : 0);
  const notVirtualizer = useVirtualizer({
    count: notInJson.length,
    getScrollElement: () => sourceRef.current,
    estimateSize: () => 34,
    scrollMargin: notOffset,
    overscan: 10,
  });
  const targetVirtualizer = useVirtualizer({
    count: targetRows.length,
    getScrollElement: () => targetRef.current,
    estimateSize: () => 40,
    overscan: 12,
  });

  const allNotInJson = useMemo(
    () =>
      hasJson
        ? sapColumns.filter((column) => !jsonSources.has(column.name))
        : [],
    [hasJson, jsonSources, sapColumns],
  );
  const jsonSourceColumns = useMemo(
    () =>
      hasJson
        ? sapColumns.filter((column) => jsonSources.has(column.name))
        : [],
    [hasJson, jsonSources, sapColumns],
  );
  const jsonSourceCount = jsonSourceColumns.length;
  const jsonSelectedCount = jsonSourceColumns.filter(
    (column) =>
      !removedRows.some(
        (row) => row.id.startsWith("json:") && row.source === column.name,
      ),
  ).length;
  const addedCount = allNotInJson.filter((column) =>
    rows.some((row) => row.origin === "USER" && row.source === column.name),
  ).length;

  const confirmReplace = async (
    currentSource: string,
    nextSource: string,
    target: string,
  ) =>
    confirmRef.current({
      title: confirmCopy.replaceMapping.title,
      description: confirmCopy.replaceMapping.description(
        currentSource,
        nextSource,
        target,
      ),
      cancelLabel: confirmCopy.replaceMapping.cancelLabel,
      confirmLabel: confirmCopy.replaceMapping.confirmLabel,
      tone: "danger",
      icon: "warning",
    });

  const startSourceDrag = (
    event: ReactPointerEvent,
    columnName: string,
    disabled: boolean,
  ) => {
    if (readOnly || disabled) return;
    event.preventDefault();
    event.stopPropagation();
    dragOrigin.current = { x: event.clientX, y: event.clientY };
    setDragFromTarget(null);
    setReorderId(null);
    setFloatLabel(columnName);
    setDragSource(columnName);
  };

  const startReorder = (event: ReactPointerEvent, row: MappingRow) => {
    if (readOnly) return;
    event.preventDefault();
    event.stopPropagation();
    dragOrigin.current = { x: event.clientX, y: event.clientY };
    setDragSource(null);
    setDragFromTarget(null);
    setFloatLabel(row.target);
    setReorderId(row.id);
  };

  const applyReorder = (movingId: string, overId: string) => {
    const visible = targetRowsRef.current;
    const fullIds = rowsRef.current.map((row) => row.id);
    const from = visible.findIndex((row) => row.id === movingId);
    const to = visible.findIndex((row) => row.id === overId);
    if (from < 0 || to < 0 || from === to) return;
    const nextVisible = visible.map((row) => row.id);
    const [moved] = nextVisible.splice(from, 1);
    nextVisible.splice(to, 0, moved);
    const queue = [...nextVisible];
    const visibleSet = new Set(nextVisible);
    const merged = fullIds.map((id) =>
      visibleSet.has(id) ? (queue.shift() as string) : id,
    );
    onReorderRef.current(merged);
    onAnnounce("Mapped column order updated");
  };

  const applySource = async (targetId: string, source: string) => {
    const row = rows.find((item) => item.id === targetId);
    if (!row || readOnly || !source) return;
    if (row.source && row.source !== source) {
      const ok = await confirmReplace(row.source, source, row.target);
      if (!ok) return;
    }
    onRepoint(targetId, source);
    onAnnounce(`${source} mapped to ${row.target}`);
    setSelectedSource(null);
  };

  useEffect(() => {
    if (!dragSource && !reorderId) return;
    dragMoved.current = false;
    dropTargetRef.current = null;
    let frame = 0;
    let latest: PointerEvent | null = null;
    const placeGhost = (x: number, y: number) => {
      const ghost = ghostRef.current;
      if (!ghost) return;
      ghost.style.transform = `translate3d(${x + 12}px, ${y + 8}px, 0)`;
    };
    placeGhost(dragOrigin.current.x, dragOrigin.current.y);
    const paint = () => {
      frame = 0;
      const event = latest;
      if (!event) return;
      if (
        Math.hypot(
          event.clientX - dragOrigin.current.x,
          event.clientY - dragOrigin.current.y,
        ) > 4
      ) {
        dragMoved.current = true;
      }
      if (!dragMoved.current) return;
      placeGhost(event.clientX, event.clientY);
      const node = document.elementFromPoint(event.clientX, event.clientY);
      const target = node?.closest("[data-target-row]") as HTMLElement | null;
      const fresh = reorderId ? null : node?.closest("[data-new-target]");
      const next = fresh ? "__new__" : target?.dataset.targetRow || null;
      if (next !== dropTargetRef.current) {
        dropTargetRef.current = next;
        setDropTargetId(next);
      }
      const tray = sourceRef.current;
      const list = targetRef.current;
      if (list) {
        const rect = list.getBoundingClientRect();
        if (event.clientY < rect.top + 36) list.scrollTop -= 14;
        else if (event.clientY > rect.bottom - 36) list.scrollTop += 14;
      }
      if (tray && !reorderId) {
        const rect = tray.getBoundingClientRect();
        if (event.clientY < rect.top + 28) tray.scrollTop -= 14;
        else if (event.clientY > rect.bottom - 28) tray.scrollTop += 14;
      }
    };
    const move = (event: PointerEvent) => {
      latest = event;
      if (!frame) frame = requestAnimationFrame(paint);
    };
    const up = (event: PointerEvent) => {
      if (frame) cancelAnimationFrame(frame);
      if (newTargetDropHandled.current) {
        newTargetDropHandled.current = false;
        dragMoved.current = false;
        setDragSource(null);
        setDragFromTarget(null);
        setReorderId(null);
        setFloatLabel(null);
        setDropTargetId(null);
        return;
      }
      const moved = dragMoved.current;
      const source = dragSource;
      const movingId = reorderId;
      const fromTarget = dragFromTarget;
      const node = document.elementFromPoint(event.clientX, event.clientY);
      const backToTray = node?.closest("[data-source-tray]");
      const fresh = node?.closest("[data-new-target]");
      const target = node?.closest("[data-target-row]") as HTMLElement | null;
      setDragSource(null);
      setDragFromTarget(null);
      setReorderId(null);
      setFloatLabel(null);
      setDropTargetId(null);
      if (
        moved &&
        movingId &&
        target?.dataset.targetRow &&
        target.dataset.targetRow !== movingId
      ) {
        applyReorder(movingId, target.dataset.targetRow);
        return;
      }
      void (async () => {
        if (moved && backToTray && fromTarget) {
          onClear(fromTarget);
          return;
        }
        if (moved && fresh && source) {
          onAddTarget(source);
          onAnnounce(`${source} added as a new target`);
          return;
        }
        if (
          moved &&
          source &&
          target?.dataset.targetRow &&
          target.dataset.targetRow !== fromTarget
        ) {
          const current = rowsRef.current.find(
            (row) => row.id === target.dataset.targetRow,
          );
          if (current?.source && current.source !== source) {
            const ok = await confirmReplace(
              current.source,
              source,
              current.target,
            );
            if (!ok) return;
          }
          onRepoint(target.dataset.targetRow, source);
          onAnnounce(`${source} mapped to ${current?.target || "target"}`);
        }
      })();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    // Callbacks are read from the drag-start render and refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragSource, reorderId]);

  const hoverRow = rows.find((row) => row.id === hoverTargetId) || null;

  useEffect(() => {
    if (!showConnectors || !hoverRow?.source) return;
    const fromIndex = fromJson.findIndex(
      (column) => column.name === hoverRow.source,
    );
    if (fromIndex >= 0) {
      const visible = fromVirtualizer
        .getVirtualItems()
        .some((item) => item.index === fromIndex);
      if (!visible) fromVirtualizer.scrollToIndex(fromIndex, { align: "auto" });
      return;
    }
    const notIndex = notInJson.findIndex(
      (column) => column.name === hoverRow.source,
    );
    if (notIndex >= 0) {
      const visible = notVirtualizer
        .getVirtualItems()
        .some((item) => item.index === notIndex);
      if (!visible) notVirtualizer.scrollToIndex(notIndex, { align: "auto" });
    }
  }, [
    fromJson,
    fromVirtualizer,
    hoverRow?.source,
    notInJson,
    notVirtualizer,
    showConnectors,
  ]);

  useEffect(() => {
    if (!showConnectors || !hoverRow?.source) {
      setConnector(null);
      return;
    }
    const draw = () => {
      const shell = shellRef.current;
      const sourceNode = sourceRef.current?.querySelector(
        `[data-source="${CSS.escape(hoverRow.source || "")}"]`,
      ) as HTMLElement | null;
      const targetNode = targetRef.current?.querySelector(
        `[data-target-row="${CSS.escape(hoverRow.id)}"]`,
      ) as HTMLElement | null;
      if (!shell || !sourceNode || !targetNode) {
        setConnector(null);
        return;
      }
      const origin = shell.getBoundingClientRect();
      const sourceBox = sourceNode.getBoundingClientRect();
      const targetBox = targetNode.getBoundingClientRect();
      setConnector({
        x1: sourceBox.right - origin.left,
        y1: sourceBox.top + sourceBox.height / 2 - origin.top,
        x2: targetBox.left - origin.left,
        y2: targetBox.top + targetBox.height / 2 - origin.top,
      });
    };
    draw();
    const sourceEl = sourceRef.current;
    const targetEl = targetRef.current;
    sourceEl?.addEventListener("scroll", draw);
    targetEl?.addEventListener("scroll", draw);
    return () => {
      sourceEl?.removeEventListener("scroll", draw);
      targetEl?.removeEventListener("scroll", draw);
    };
  }, [
    hoverRow,
    showConnectors,
    fromJson.length,
    notInJson.length,
    targetRows.length,
  ]);

  const renderSource = (
    column: SapColumn,
    mode: "json" | "extra" | "plain",
  ) => {
    const mapped = rows.some((row) => row.source === column.name);
    const removed =
      mode === "json" &&
      removedRows.some(
        (row) => row.id.startsWith("json:") && row.source === column.name,
      );
    const added =
      mode === "extra" &&
      rows.some((row) => row.origin === "USER" && row.source === column.name);
    const highlighted = hoverRow?.source === column.name;
    return (
      <Flex
        key={column.name}
        data-source={column.name}
        h="34px"
        align="center"
        gap={2}
        px={2}
        bg={highlighted ? "purple.50" : "transparent"}
        opacity={removed ? 0.7 : 1}
        cursor="default"
        onClick={() => {
          if (!removed) setSelectedSource(column.name);
        }}
        role="option"
        aria-selected={selectedSource === column.name}
      >
        <Box
          as="span"
          role="button"
          tabIndex={readOnly || removed ? -1 : 0}
          aria-label={`Drag ${column.name} to a mapped column`}
          flexShrink={0}
          display="inline-flex"
          alignItems="center"
          justifyContent="center"
          w="22px"
          h="22px"
          borderRadius="sm"
          color="gray.400"
          cursor={readOnly || removed ? "default" : "grab"}
          _hover={
            readOnly || removed
              ? undefined
              : { color: "gray.600", bg: "gray.100" }
          }
          style={{ touchAction: "none" }}
          onPointerDown={(event) =>
            startSourceDrag(event, column.name, removed)
          }
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (readOnly || removed) return;
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              setSelectedSource(column.name);
            }
          }}
        >
          <MdDragIndicator aria-hidden />
        </Box>
        {mode !== "plain" && (
          <input
            type="checkbox"
            aria-label={
              mode === "json" ? `Include ${column.name}` : `Add ${column.name}`
            }
            checked={mode === "json" ? !removed : added}
            disabled={readOnly}
            onChange={(event) => {
              if (mode === "json") {
                onToggleJsonSource(column.name, event.target.checked);
                return;
              }
              onToggleSource(column.name, event.target.checked);
            }}
            onPointerDown={(event) => event.stopPropagation()}
          />
        )}
        <Tooltip content={column.name}>
          <Text
            fontSize="sm"
            fontWeight="medium"
            truncate
            flex="1"
            minW={0}
            color={removed ? "gray.400" : undefined}
            textDecoration={removed ? "line-through" : undefined}
          >
            {column.name}
          </Text>
        </Tooltip>
        {highlighted && hoverRow && (
          <Text
            fontSize="xs"
            color="purple.700"
            flexShrink={0}
            bg="white"
            px={1}
            borderRadius="sm"
          >
            → {hoverRow.target}
          </Text>
        )}
        {added && (
          <Badge
            size="sm"
            variant="subtle"
            colorPalette="purple"
            flexShrink={0}
          >
            added
          </Badge>
        )}
        {removed && (
          <Badge size="sm" variant="subtle" colorPalette="gray" flexShrink={0}>
            removed
          </Badge>
        )}
        <Text fontSize="xs" color="gray.500" flexShrink={0}>
          {typeLabel(column)}
        </Text>
        <Box
          boxSize="8px"
          borderRadius="full"
          bg={mapped && !removed ? "purple.600" : "transparent"}
          borderWidth="1.5px"
          borderColor={mapped && !removed ? "purple.600" : "gray.300"}
          flexShrink={0}
        />
      </Flex>
    );
  };

  const renderTarget = (row: MappingRow) => {
    const sourceColumn = row.source ? columnByName.get(row.source) : undefined;
    const rowType = targetTypeByName.get(row.target) || typeLabel(sourceColumn);
    const namesMatch = Boolean(row.source) && row.source === row.target;
    const dropping = Boolean(dragSource) && dropTargetId === row.id;
    const replacing =
      dropping && row.source && dragSource && row.source !== dragSource;
    const nameError = row.targetLocked
      ? null
      : targetNameError(row.target, rows, row.id);
    const fromScript = row.status === "From script" && !row.source;
    return (
      <Box
        key={row.id}
        data-target-row={row.id}
        h={nameError ? "auto" : "40px"}
        minH="40px"
        px={2}
        bg={
          reorderId && dropTargetId === row.id
            ? "purple.100"
            : hoverTargetId === row.id
              ? "purple.50"
              : "white"
        }
        boxShadow={
          reorderId && dropTargetId === row.id
            ? "inset 0 2px 0 #7c3aed"
            : undefined
        }
        borderBottomWidth="1px"
        borderColor="gray.100"
        onMouseEnter={() => setHoverTargetId(row.id)}
        onMouseLeave={() =>
          setHoverTargetId((current) => (current === row.id ? null : current))
        }
        onClick={() => {
          if (selectedSource) applySource(row.id, selectedSource);
        }}
      >
        <Flex h="40px" align="center" gap={2}>
          <Box
            as="span"
            role="button"
            tabIndex={readOnly ? -1 : 0}
            aria-label={`Reorder ${row.target}`}
            flexShrink={0}
            display="inline-flex"
            alignItems="center"
            justifyContent="center"
            w="18px"
            h="28px"
            color="gray.400"
            cursor={readOnly ? "default" : "grab"}
            style={{ touchAction: "none" }}
            onPointerDown={(event) => startReorder(event, row)}
            onClick={(event) => event.stopPropagation()}
          >
            <MdDragIndicator aria-hidden />
          </Box>
          {!row.targetLocked ? (
            <NativeSelect.Root
              size="xs"
              width="34%"
              flexShrink={0}
              disabled={readOnly}
            >
              <NativeSelect.Field
                aria-label={`Source column for ${row.target}`}
                value={row.source || ""}
                title={row.source || "Choose source column"}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => {
                  if (event.target.value)
                    applySource(row.id, event.target.value);
                }}
                style={{
                  color: "#1a202c",
                  backgroundColor: "white",
                  fontSize: "13px",
                }}
              >
                <option value="">none</option>
                {hasJson ? (
                  <>
                    <optgroup label="From file">
                      {fileColumns.map((column) => (
                        <option key={column.name} value={column.name}>
                          {column.name}
                        </option>
                      ))}
                    </optgroup>
                    <optgroup label="Not in file">
                      {extraColumns.map((column) => (
                        <option key={column.name} value={column.name}>
                          {column.name}
                        </option>
                      ))}
                    </optgroup>
                  </>
                ) : (
                  sapColumns.map((column) => (
                    <option key={column.name} value={column.name}>
                      {column.name}
                    </option>
                  ))
                )}
              </NativeSelect.Field>
            </NativeSelect.Root>
          ) : (
            <Flex
              w="34%"
              minW={0}
              h="28px"
              align="center"
              gap={1}
              px={2}
              borderRadius="md"
              borderWidth="1px"
              borderStyle={dragSource || !row.source ? "dashed" : "solid"}
              borderColor={dragSource ? "purple.300" : "gray.200"}
              bg={dropping ? "purple.50" : "white"}
              cursor={row.source && !readOnly ? "grab" : "default"}
              flexShrink={0}
              onPointerDown={(event) => {
                if (readOnly || !row.source) return;
                event.stopPropagation();
                dragOrigin.current = { x: event.clientX, y: event.clientY };
                setDragFromTarget(row.id);
                setDragSource(row.source);
              }}
            >
              {row.source && <MdDragIndicator color="#9ca3af" />}
              <Tooltip
                content={
                  dropping
                    ? "Drop SAP column here"
                    : row.source || (fromScript ? "none" : "Unmapped")
                }
              >
                <Text fontSize="sm" truncate>
                  {dropping
                    ? "Drop SAP column here"
                    : row.source || (fromScript ? "none" : "Unmapped")}
                </Text>
              </Tooltip>
            </Flex>
          )}
          <Flex
            boxSize="20px"
            borderRadius="full"
            align="center"
            justify="center"
            bg={
              fromScript ? "purple.50" : namesMatch ? "gray.100" : "purple.50"
            }
            color={namesMatch ? "gray.500" : "purple.600"}
            fontSize="xs"
            flexShrink={0}
          >
            {fromScript ? "ƒ" : "→"}
          </Flex>
          <Flex flex="1" minW={0} align="center" gap={2}>
            {row.targetLocked ? (
              <Tooltip content="Defined by the mapping JSON">
                <Box color="gray.500" aria-label="Defined by the mapping JSON">
                  <FiLock size={12} />
                </Box>
              </Tooltip>
            ) : null}
            {row.targetLocked ? (
              <Tooltip content={row.target}>
                <Text fontSize="sm" fontWeight="medium" truncate>
                  {row.target}
                </Text>
              </Tooltip>
            ) : editingId === row.id ? (
              <input
                value={editValue}
                autoFocus
                aria-label={`Rename ${row.target}`}
                onChange={(event) => setEditValue(event.target.value)}
                onClick={(event) => event.stopPropagation()}
                onBlur={() => {
                  onRename(row.id, editValue);
                  setEditingId(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    onRename(row.id, editValue);
                    setEditingId(null);
                  }
                }}
                style={{
                  flex: 1,
                  minWidth: 0,
                  border: "1px solid #7c3aed",
                  borderRadius: 6,
                  padding: "2px 6px",
                  font: "inherit",
                  fontSize: 14,
                }}
              />
            ) : (
              <Tooltip content={row.target}>
                <Text
                  fontSize="sm"
                  fontWeight="medium"
                  truncate
                  borderWidth="1px"
                  borderColor="purple.400"
                  borderRadius="md"
                  px={2}
                  cursor={readOnly ? "default" : "text"}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (readOnly) return;
                    setEditingId(row.id);
                    setEditValue(row.target);
                  }}
                >
                  {row.target}
                </Text>
              </Tooltip>
            )}
          </Flex>
          {rowType && (
            <Tooltip content={rowType}>
              <Text
                fontSize="xs"
                color="gray.600"
                flexShrink={0}
                w="72px"
                truncate
              >
                {rowType}
              </Text>
            </Tooltip>
          )}
          {replacing ? (
            <Text fontSize="xs" color="purple.700" flexShrink={0}>
              Will replace {row.source}
            </Text>
          ) : (
            <Badge
              size="sm"
              variant="subtle"
              colorPalette={STATUS_PALETTE[row.status] || "gray"}
              flexShrink={0}
            >
              {row.status}
            </Badge>
          )}
          {!readOnly && (
            <Tooltip content="Remove from mapping">
              <IconButton
                aria-label={`Remove ${row.target} from mapping`}
                size="xs"
                variant="ghost"
                onClick={(event) => {
                  event.stopPropagation();
                  onClear(row.id);
                }}
              >
                <FiX />
              </IconButton>
            </Tooltip>
          )}
        </Flex>
        {nameError && (
          <Text fontSize="xs" color="red.600" pb={1}>
            {nameError}
          </Text>
        )}
      </Box>
    );
  };

  const paneHeight = paneMaxHeight === "100%" ? "100%" : paneMaxHeight;

  return (
    <Box h={paneHeight} minH={paneHeight === "100%" ? 0 : paneHeight}>
      <Flex
        ref={shellRef}
        position="relative"
        gap={3}
        align="stretch"
        h="100%"
        minH={0}
      >
        <Box
          data-source-tray
          w="38%"
          minW={0}
          h="100%"
          minH={0}
          display="flex"
          flexDirection="column"
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="md"
          overflow="hidden"
        >
          <Flex
            px={2}
            h="32px"
            align="center"
            justify="space-between"
            bg="gray.50"
          >
            <Text fontSize="xs" fontWeight="semibold">
              SAP source columns {sapColumns.length}
            </Text>
            <Text fontSize="xs" color="gray.500">
              Drag onto a target →
            </Text>
          </Flex>
          <Box
            ref={sourceRef}
            flex="1"
            minH={0}
            overflowY="auto"
            overflowX="hidden"
            css={thinScroll}
            role="listbox"
            aria-label="SAP source columns"
          >
            {hasJson && (
              <Flex
                position="sticky"
                top={0}
                zIndex={1}
                h={`${JSON_GROUP_HEADER}px`}
                px={2}
                align="center"
                gap={2}
                bg="purple.50"
              >
                <Text fontSize="xs" fontWeight="medium">
                  From JSON · {jsonSelectedCount} of {jsonSourceCount} selected
                </Text>
                <Button
                  size="xs"
                  variant="ghost"
                  ml="auto"
                  disabled={readOnly}
                  onClick={onSelectAllJson}
                >
                  Select all
                </Button>
                <Text fontSize="xs" color="gray.400">
                  ·
                </Text>
                <Button
                  size="xs"
                  variant="ghost"
                  disabled={readOnly}
                  onClick={onClearJson}
                >
                  Clear
                </Button>
              </Flex>
            )}
            <Box h={`${fromVirtualizer.getTotalSize()}px`} position="relative">
              {fromVirtualizer.getVirtualItems().map((item) => (
                <Box
                  key={fromJson[item.index].name}
                  position="absolute"
                  top={0}
                  left={0}
                  right={0}
                  h="34px"
                  transform={`translateY(${item.start - (hasJson ? JSON_GROUP_HEADER : 0)}px)`}
                >
                  {renderSource(
                    fromJson[item.index],
                    hasJson ? "json" : "plain",
                  )}
                </Box>
              ))}
            </Box>
            {hasJson && (
              <Flex
                position="sticky"
                top={0}
                zIndex={1}
                h="32px"
                px={2}
                align="center"
                gap={2}
                bg="purple.50"
              >
                <Text fontSize="xs" fontWeight="medium">
                  Not in JSON · {allNotInJson.length}
                </Text>
                <Text fontSize="xs" color="gray.500">
                  {addedCount} of {allNotInJson.length} added
                </Text>
                <Button
                  size="xs"
                  variant="ghost"
                  ml="auto"
                  disabled={readOnly}
                  onClick={() =>
                    onSelectAll(allNotInJson.map((column) => column.name))
                  }
                >
                  Select all
                </Button>
              </Flex>
            )}
            <Box h={`${notVirtualizer.getTotalSize()}px`} position="relative">
              {notVirtualizer.getVirtualItems().map((item) => (
                <Box
                  key={notInJson[item.index].name}
                  position="absolute"
                  top={0}
                  left={0}
                  right={0}
                  h="34px"
                  transform={`translateY(${item.start - notOffset}px)`}
                >
                  {renderSource(notInJson[item.index], "extra")}
                </Box>
              ))}
            </Box>
            {sapColumns.length === 0 && (
              <Text fontSize="sm" color="gray.500" p={3}>
                Using same names as SAP. Add a mapping JSON to customise.
              </Text>
            )}
          </Box>
        </Box>

        <Box
          flex="1"
          minW={0}
          h="100%"
          minH={0}
          display="flex"
          flexDirection="column"
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="md"
          overflow="hidden"
        >
          <Flex px={2} h="32px" align="center" bg="gray.50">
            <Text fontSize="xs" fontWeight="semibold">
              Mapped columns {rows.length}
            </Text>
            <Text fontSize="xs" color="gray.500" ml="auto">
              Drag the handle to reorder
            </Text>
          </Flex>
          <Box
            ref={targetRef}
            flex="1"
            minH={0}
            overflowY="auto"
            overflowX="hidden"
            css={thinScroll}
            role="listbox"
            aria-label="Mapped columns"
          >
            <Box
              h={`${targetVirtualizer.getTotalSize()}px`}
              position="relative"
            >
              {targetVirtualizer.getVirtualItems().map((item) => (
                <Box
                  key={targetRows[item.index].id}
                  data-index={item.index}
                  ref={targetVirtualizer.measureElement}
                  position="absolute"
                  top={0}
                  left={0}
                  right={0}
                  transform={`translateY(${item.start}px)`}
                >
                  {renderTarget(targetRows[item.index])}
                </Box>
              ))}
            </Box>
            {(filter === "removed" || removedRows.length > 0) && (
              <Box borderTopWidth="1px" borderColor="gray.100">
                <Flex px={2} h="36px" align="center" gap={2} bg="gray.50">
                  <Button
                    size="xs"
                    variant="ghost"
                    aria-expanded={removedOpen || filter === "removed"}
                    onClick={() => setRemovedOpen((open) => !open)}
                  >
                    Removed from mapping · {removedRows.length}
                  </Button>
                  <Button
                    size="xs"
                    variant="ghost"
                    ml="auto"
                    disabled={readOnly || removedRows.length === 0}
                    onClick={onRestoreAll}
                  >
                    Restore all
                  </Button>
                </Flex>
                {(removedOpen || filter === "removed") &&
                  removedRows.map((row) => {
                    const type =
                      targetTypeByName.get(row.target) ||
                      typeLabel(
                        row.source ? columnByName.get(row.source) : undefined,
                      );
                    return (
                      <Box
                        key={row.id}
                        px={2}
                        py={2}
                        borderTopWidth="1px"
                        borderColor="gray.100"
                      >
                        <Flex align="center" gap={2}>
                          <Text fontSize="sm" truncate>
                            {row.source || "none"} → {row.target}
                          </Text>
                          {type && (
                            <Text fontSize="xs" color="gray.500">
                              {type}
                            </Text>
                          )}
                          <Button
                            size="xs"
                            variant="ghost"
                            ml="auto"
                            disabled={readOnly}
                            onClick={() => onRestore(row.id)}
                          >
                            Restore
                          </Button>
                        </Flex>
                        {!row.source && row.status === "From script" && (
                          <Text fontSize="xs" color="orange.600">
                            The script may still produce this column.
                          </Text>
                        )}
                      </Box>
                    );
                  })}
              </Box>
            )}
            {filter !== "removed" && (
              <Flex
                data-new-target
                position="sticky"
                bottom={0}
                zIndex={2}
                m={2}
                minH="48px"
                flexDirection="column"
                align="center"
                justify="center"
                gap={0}
                px={2}
                py={1}
                borderWidth="1px"
                borderStyle="dashed"
                borderColor={
                  dropTargetId === "__new__" ? "purple.500" : "purple.200"
                }
                borderRadius="md"
                color="purple.700"
                fontSize="sm"
                bg={dropTargetId === "__new__" ? "purple.50" : "white"}
                cursor={
                  readOnly ? "default" : selectedSource ? "pointer" : "default"
                }
                onClick={() => {
                  if (suppressNewTargetClick.current) {
                    suppressNewTargetClick.current = false;
                    return;
                  }
                  if (readOnly || !selectedSource) return;
                  onAddTarget(selectedSource);
                  onAnnounce(`${selectedSource} added as a new target`);
                  setSelectedSource(null);
                }}
                onPointerUp={() => {
                  if (readOnly || !dragSource || !dragMoved.current) return;
                  const source = dragSource;
                  newTargetDropHandled.current = true;
                  suppressNewTargetClick.current = true;
                  setDragSource(null);
                  setDragFromTarget(null);
                  setFloatLabel(null);
                  setDropTargetId(null);
                  onAddTarget(source);
                  onAnnounce(`${source} added as a new target`);
                }}
              >
                <Text>+ New target column · drop a SAP column here</Text>
                <Text fontSize="xs" color="purple.500">
                  Select a SAP column, then click here or drop it.
                </Text>
              </Flex>
            )}
          </Box>
        </Box>

        {showConnectors && connector && (
          <svg
            aria-hidden
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              pointerEvents: "none",
            }}
          >
            <path
              d={`M ${connector.x1} ${connector.y1} C ${(connector.x1 + connector.x2) / 2} ${connector.y1}, ${(connector.x1 + connector.x2) / 2} ${connector.y2}, ${connector.x2} ${connector.y2}`}
              fill="none"
              stroke="#7c3aed"
              strokeWidth="1.25"
            />
          </svg>
        )}
      </Flex>
      <Flex mt={2} justify="space-between" align="center">
        <Text fontSize="xs" color="gray.500">
          Connectors are shown for the hovered row only. Locked names come from
          the JSON.
        </Text>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => setShowConnectors((value) => !value)}
        >
          {showConnectors ? "Hide connectors" : "Show connectors"}
        </Button>
      </Flex>
      {floatLabel && (
        <Flex
          ref={ghostRef}
          position="fixed"
          left={0}
          top={0}
          zIndex={2000}
          pointerEvents="none"
          align="center"
          gap={1}
          px={2}
          h="28px"
          borderRadius="md"
          bg="purple.600"
          color="white"
          fontSize="sm"
          fontWeight="medium"
          style={{
            transform: "translate3d(-9999px, -9999px, 0)",
            willChange: "transform",
          }}
        >
          <MdDragIndicator />
          {floatLabel}
        </Flex>
      )}
    </Box>
  );
};

export default ColumnMapper;
