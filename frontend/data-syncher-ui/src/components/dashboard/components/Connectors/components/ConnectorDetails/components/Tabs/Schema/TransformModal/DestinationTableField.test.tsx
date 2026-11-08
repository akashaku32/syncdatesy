/**
 * @vitest-environment jsdom
 */
import { type ComponentProps } from "react";

import { ChakraProvider } from "@chakra-ui/react";

import { afterEach, describe, expect, it, vi } from "vitest";

import theme from "@/theme/theme";

import DestinationTableField from "./DestinationTableField";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

const renderField = (
  props: Partial<ComponentProps<typeof DestinationTableField>> = {},
) =>
  render(
    <ChakraProvider value={theme}>
      <DestinationTableField
        destinationTableName="CMFIGL04"
        destinationConnectionName="Snowflake Prod"
        jsonTableName="/IMO/CMFIGL04"
        source="JSON"
        defaultDisplayName="0FI_GL_4"
        isEditing={false}
        editValue="CMFIGL04"
        error={null}
        warnings={[]}
        existingSnowflakeTable={false}
        confirmUseExisting={false}
        onUseDefault={vi.fn()}
        onStartEdit={vi.fn()}
        onEditChange={vi.fn()}
        onCommitEdit={vi.fn()}
        onCancelEdit={vi.fn()}
        onConfirmUseExisting={vi.fn()}
        onChooseAnotherName={vi.fn()}
        {...props}
      />
    </ChakraProvider>,
  );

describe("DestinationTableField", () => {
  it("shows JSON, Default, and Edited tags", () => {
    const { rerender } = renderField({ source: "JSON" });
    expect(screen.getByText("From JSON")).toBeTruthy();
    rerender(
      <ChakraProvider value={theme}>
        <DestinationTableField
          destinationTableName="CMFIGL04"
          jsonTableName={null}
          source="DEFAULT"
          defaultDisplayName="CMFIGL04"
          isEditing={false}
          editValue=""
          error={null}
          warnings={[]}
          existingSnowflakeTable={false}
          confirmUseExisting={false}
          onUseDefault={vi.fn()}
          onStartEdit={vi.fn()}
          onEditChange={vi.fn()}
          onCommitEdit={vi.fn()}
          onCancelEdit={vi.fn()}
          onConfirmUseExisting={vi.fn()}
          onChooseAnotherName={vi.fn()}
        />
      </ChakraProvider>,
    );
    expect(screen.getByText("Default")).toBeTruthy();
    rerender(
      <ChakraProvider value={theme}>
        <DestinationTableField
          destinationTableName="CMFIGL04"
          jsonTableName={null}
          source="MANUAL"
          defaultDisplayName="0FI_GL_4"
          isEditing={false}
          editValue=""
          error={null}
          warnings={[]}
          existingSnowflakeTable={false}
          confirmUseExisting={false}
          onUseDefault={vi.fn()}
          onStartEdit={vi.fn()}
          onEditChange={vi.fn()}
          onCommitEdit={vi.fn()}
          onCancelEdit={vi.fn()}
          onConfirmUseExisting={vi.fn()}
          onChooseAnotherName={vi.fn()}
        />
      </ChakraProvider>,
    );
    expect(screen.getByText("Edited")).toBeTruthy();
  });

  it("copies the name and sticks to the top of the step", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderField();
    const banner = screen.getByTestId("destination-banner");
    expect(banner.style.position).toBe("sticky");
    fireEvent.click(
      screen.getByRole("button", { name: "Copy destination table name" }),
    );
    expect(writeText).toHaveBeenCalledWith("CMFIGL04");
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  });

  it("shows the validation error and commits an edit", () => {
    const onCommitEdit = vi.fn();
    const onEditChange = vi.fn();
    renderField({
      isEditing: true,
      editValue: "bad name",
      error: "Destination table name is invalid.",
      onCommitEdit,
      onEditChange,
    });
    expect(screen.getByText("Destination table name is invalid.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Destination table name"), {
      target: { value: "OK_NAME" },
    });
    expect(onEditChange).toHaveBeenCalledWith("OK_NAME");
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onCommitEdit).toHaveBeenCalled();
  });

  it("labels Databricks destinations as Unity Catalog table name", () => {
    renderField({ destinationConnectionName: "Databricks Warehouse" });
    expect(screen.getByText("Unity Catalog table name")).toBeTruthy();
  });

  it("labels Snowflake destinations as Destination table in Snowflake", () => {
    renderField({ destinationConnectionName: "Snowflake Prod" });
    expect(screen.getByText("Destination table in Snowflake")).toBeTruthy();
  });
});
