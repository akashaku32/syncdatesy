/**
 * @vitest-environment jsdom
 */
import { type ComponentProps } from "react";

import { ChakraProvider } from "@chakra-ui/react";

import { afterEach, describe, expect, it, vi } from "vitest";

import theme from "@/theme/theme";

import ColumnMapper from "./ColumnMapper";
import { type MappingRow } from "./mappingState";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: (options: { count: number; estimateSize?: () => number }) => {
    const size = options.estimateSize?.() ?? 40;
    return {
      getTotalSize: () => options.count * size,
      getVirtualItems: () =>
        Array.from({ length: options.count }, (_, index) => ({
          index,
          start: index * size,
          size,
          key: index,
        })),
      measureElement: () => undefined,
      scrollToIndex: () => undefined,
    };
  },
}));

afterEach(() => {
  cleanup();
});

const row = (
  partial: Partial<MappingRow> & Pick<MappingRow, "id" | "target">,
): MappingRow => ({
  source: partial.target,
  origin: "JSON",
  targetLocked: true,
  overridden: false,
  status: "Direct",
  ...partial,
});

const renderMapper = (
  props: Partial<ComponentProps<typeof ColumnMapper>> = {},
) => {
  const handlers = {
    onToggleSource: vi.fn(),
    onToggleJsonSource: vi.fn(),
    onRepoint: vi.fn(),
    onRename: vi.fn(),
    onClear: vi.fn(),
    onAddTarget: vi.fn(),
    onSelectAll: vi.fn(),
    onSelectAllJson: vi.fn(),
    onClearJson: vi.fn(),
    onRestore: vi.fn(),
    onRestoreAll: vi.fn(),
    onReorder: vi.fn(),
    onAnnounce: vi.fn(),
    confirm: vi.fn().mockResolvedValue(true),
  };
  render(
    <ChakraProvider value={theme}>
      <ColumnMapper
        rows={[row({ id: "json:DMBTR", target: "DMBTR", source: "DMBTR" })]}
        removedRows={[
          row({ id: "json:BUKRS", target: "BUKRS", source: "BUKRS" }),
        ]}
        sapColumns={[
          { name: "BUKRS", type: "CHAR" },
          { name: "DMBTR", type: "CURR" },
        ]}
        jsonRaw={{
          field_mapping: [
            { src_fieldnames: ["BUKRS"], tgt_fieldname: "BUKRS" },
            { src_fieldnames: ["DMBTR"], tgt_fieldname: "DMBTR" },
          ],
        }}
        search=""
        filter="all"
        {...handlers}
        {...props}
      />
    </ChakraProvider>,
  );
  return handlers;
};

describe("ColumnMapper removals", () => {
  it("labels JSON checkboxes and remove buttons", () => {
    const handlers = renderMapper();
    expect(screen.getByText(/1 of 2 selected/)).toBeTruthy();
    const include = screen.getByRole("checkbox", { name: "Include BUKRS" });
    expect((include as HTMLInputElement).checked).toBe(false);
    fireEvent.click(include);
    expect(handlers.onToggleJsonSource).toHaveBeenCalledWith("BUKRS", true);
    expect(screen.getByText("removed")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Remove DMBTR from mapping" }),
    );
    expect(handlers.onClear).toHaveBeenCalledWith("json:DMBTR");
    expect(screen.getByText("Removed from mapping · 1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(handlers.onRestore).toHaveBeenCalledWith("json:BUKRS");
  });

  it("shows removed rows for the Removed filter", () => {
    renderMapper({ filter: "removed" });
    expect(screen.getByText("BUKRS → BUKRS")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Remove DMBTR from mapping" }),
    ).toBeNull();
  });

  it("exposes a drag handle on each SAP source row", () => {
    renderMapper();
    expect(
      screen.getByRole("button", { name: "Drag DMBTR to a mapped column" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Drag BUKRS to a mapped column" }),
    ).toBeTruthy();
  });

  it("adds a new target from the sticky strip after selecting a SAP column", () => {
    const handlers = renderMapper();
    expect(
      screen.getByText("Select a SAP column, then click here or drop it."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: /DMBTR/ }));
    fireEvent.click(
      screen.getByText("+ New target column · drop a SAP column here"),
    );
    expect(handlers.onAddTarget).toHaveBeenCalledWith("DMBTR");
  });
});
