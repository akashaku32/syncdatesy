import { useEffect, useMemo, useRef, useState } from "react";

import { Box, Button, HStack } from "@chakra-ui/react";

import {
  type ExcelBodyStyle,
  type ExcelConditionalFormat,
  type ExcelDifferentialStyle,
  type ExcelHeaderStyle,
  type ExcelOptions,
} from "@/types/connectors";

import {
  DEFAULT_EXCEL_OPTIONS,
  DEFAULT_HEADER_STYLE,
  DEFAULT_SHEET_HEADER_STYLE,
  type ExcelSettingsProps,
  cleanRulePayload,
  getColumnDataType,
  isOperatorAllowed,
} from "./helpers";
import BasicOptionsTab from "./tabs/BasicOptionsTab";
import ConditionalFormattingTab from "./tabs/ConditionalFormattingTab";
import StylingTab from "./tabs/StylingTab";

// Re-export constants, utilities, and types for full backward compatibility
// eslint-disable-next-line react-refresh/only-export-components
export * from "./helpers";

export default function ExcelSettings({
  excelOptions,
  excelSheetName,
  conditionalFormats = [],
  tableFields,
  selectedFields,
  tableName,
  onChange,
}: ExcelSettingsProps) {
  const [activeTab, setActiveTab] = useState<
    "basic" | "styling" | "formatting"
  >("basic");

  const options = excelOptions || DEFAULT_EXCEL_OPTIONS;
  const headerStyle = options.header_style || DEFAULT_HEADER_STYLE;
  const sheetHeaderStyle =
    options.sheet_header_style || DEFAULT_SHEET_HEADER_STYLE;

  const colNames = useMemo(() => {
    if (selectedFields && selectedFields.length > 0) {
      const tableKeys = Object.keys(tableFields || {});
      if (tableKeys.length === 0) return selectedFields;
      const valid = selectedFields.filter(
        (col) =>
          tableFields[col] !== undefined ||
          tableKeys.some((k) => k.toLowerCase() === col.toLowerCase()),
      );
      if (valid.length > 0) return valid;
      return selectedFields;
    }
    return Object.keys(tableFields || {});
  }, [selectedFields, tableFields]);

  const rules = conditionalFormats || [];

  const prevRulesLengthRef = useRef(rules.length);

  useEffect(() => {
    if (rules.length > prevRulesLengthRef.current) {
      setTimeout(() => {
        const lastRuleEl = document.getElementById(
          `excel-rule-${rules.length - 1}`,
        );
        if (lastRuleEl) {
          lastRuleEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      }, 50);
    }
    prevRulesLengthRef.current = rules.length;
  }, [rules.length]);

  const sheetName = options.sheet_name || excelSheetName || "";
  const isSheetHeaderEnabled = !!(
    options.sheet_header_enabled ?? options.sheet_header !== undefined
  );

  const updateOptions = (patch: Partial<ExcelOptions>) => {
    const nextOptions = {
      ...options,
      ...patch,
    };

    if (nextOptions.sheet_header_enabled === false) {
      delete nextOptions.sheet_header;
      delete nextOptions.sheet_header_style;
      delete nextOptions.sheet_header_row_span;
      delete (nextOptions as Record<string, unknown>).sheet_header_title;
      delete (nextOptions as Record<string, unknown>).sheet_header_merge_rows;
    }

    onChange({
      excel_options: nextOptions,
      ...(patch.sheet_name !== undefined
        ? { excel_sheet_name: patch.sheet_name }
        : {}),
    });
  };

  const updateHeaderStyle = (patch: Partial<ExcelHeaderStyle>) => {
    const cleanPatch = { ...patch };
    if (cleanPatch.fill) {
      cleanPatch.fill = cleanPatch.fill.replace("#", "");
    }
    if (cleanPatch.font_color) {
      cleanPatch.font_color = cleanPatch.font_color.replace("#", "");
    }
    onChange({
      excel_options: {
        ...options,
        header_style: {
          ...headerStyle,
          ...cleanPatch,
        },
      },
    });
  };

  const updateSheetHeaderStyle = (patch: Partial<ExcelHeaderStyle>) => {
    const cleanPatch = { ...patch };
    if (cleanPatch.fill) {
      cleanPatch.fill = cleanPatch.fill.replace("#", "");
    }
    if (cleanPatch.font_color) {
      cleanPatch.font_color = cleanPatch.font_color.replace("#", "");
    }
    onChange({
      excel_options: {
        ...options,
        sheet_header_style: {
          ...sheetHeaderStyle,
          ...cleanPatch,
        },
      },
    });
  };

  const bodyStyle = options.body_style || {};

  const updateBodyStyle = (patch: Partial<ExcelBodyStyle>) => {
    const cleanPatch = { ...patch };
    if (cleanPatch.fill) {
      const cleanFill = cleanPatch.fill.replace("#", "");
      cleanPatch.fill = cleanFill;
      cleanPatch.fill_color = cleanFill;
      cleanPatch.background_color = cleanFill;
    }
    if (cleanPatch.banding_fill) {
      const cleanBandingFill = cleanPatch.banding_fill.replace("#", "");
      cleanPatch.banding_fill = cleanBandingFill;
      cleanPatch.banding_fill_color = cleanBandingFill;
      cleanPatch.banding_background_color = cleanBandingFill;
    }
    if (cleanPatch.font_color) {
      cleanPatch.font_color = cleanPatch.font_color.replace("#", "");
    }
    if (cleanPatch.banding_font_color) {
      cleanPatch.banding_font_color = cleanPatch.banding_font_color.replace(
        "#",
        "",
      );
    }

    updateOptions({
      body_style: {
        ...bodyStyle,
        ...cleanPatch,
      },
    });
  };

  const handleClearBanding = () => {
    onChange({
      excel_options: {
        ...options,
        body_style: {
          ...bodyStyle,
          banding_fill: undefined,
          banding_fill_color: undefined,
          banding_background_color: undefined,
          banding_font_color: undefined,
          banding_frequency: undefined,
        },
      },
    });
  };

  const clean = (r: ExcelConditionalFormat) =>
    cleanRulePayload(r, colNames, tableFields);

  const handleAddRule = () => {
    const newRule: ExcelConditionalFormat = {
      type: "cellis",
      column_name: colNames[0] || "",
    };
    onChange({
      excel_conditional_formats: [...rules, newRule].map(clean),
    });
  };

  const handleUpdateRule = (
    index: number,
    patch: Partial<ExcelConditionalFormat>,
  ) => {
    const updated = [...rules];
    const targetType = patch.type ? patch.type : updated[index].type;
    let targetOperator =
      patch.operator !== undefined ? patch.operator : updated[index].operator;

    const evalCol =
      patch.condition_column_name !== undefined
        ? patch.condition_column_name
        : patch.column_name !== undefined
          ? patch.column_name
          : updated[index].condition_column_name ||
            updated[index].column_name ||
            "";
    const rawType = tableFields[evalCol] || "";
    const colDataType = getColumnDataType(rawType);

    if (targetOperator && !isOperatorAllowed(targetOperator, colDataType)) {
      targetOperator = "equal";
    }

    if (patch.type && patch.type !== updated[index].type) {
      const colName =
        patch.column_name || updated[index].column_name || colNames[0] || "";
      if (patch.type === "multipleconditions") {
        updated[index] = {
          type: "multipleconditions",
          column_name: colName,
          highlight_scope: "entire_row",
          target_columns: colName ? [colName] : [],
          conditions: [
            {
              column: colName,
              operator: "equal",
              value: "",
            },
          ],
          style: {
            fill: "FFC7CE",
            font_color: "9C0006",
          },
        };
      } else {
        updated[index] = {
          type: targetType,
          column_name: colName,
          operator: targetOperator,
        };
      }
    } else {
      updated[index] = {
        ...updated[index],
        ...patch,
        type: targetType,
        operator: targetOperator,
      };
    }

    onChange({
      excel_conditional_formats: updated.map(clean),
    });
  };

  const handleUpdateRuleStyle = (
    index: number,
    patch: Partial<ExcelDifferentialStyle>,
  ) => {
    const updated = [...rules];
    const newStyle = {
      ...updated[index].style,
      ...patch,
    };
    if (newStyle.fill) {
      const cleanFill = newStyle.fill.replace("#", "");
      newStyle.fill = cleanFill;
      newStyle.fill_color = cleanFill;
      newStyle.background_color = cleanFill;
    }
    if (newStyle.font_color) {
      newStyle.font_color = newStyle.font_color.replace("#", "");
    }
    updated[index] = {
      ...updated[index],
      style: newStyle,
    };
    onChange({
      excel_conditional_formats: updated.map(clean),
    });
  };

  const handleDeleteRule = (e: React.MouseEvent, index: number) => {
    e.stopPropagation();
    onChange({
      excel_conditional_formats: rules.filter((_, i) => i !== index).map(clean),
    });
  };

  return (
    <Box
      mt={2}
      p={2}
      border="1px solid"
      borderColor="gray.200"
      borderRadius="md"
      bg="gray.50"
      className="excel-settings-compact"
    >
      <style
        dangerouslySetInnerHTML={{
          __html: `
        .excel-settings-compact {
          --gap: 6px !important;
          --grid-gap: 6px !important;
          padding: 8px !important;
          margin-top: 6px !important;
        }
        .excel-settings-compact input,
        .excel-settings-compact select {
          height: 24px !important;
          padding-top: 2px !important;
          padding-bottom: 2px !important;
          font-size: 11px !important;
        }
        .excel-settings-compact label,
        .excel-settings-compact p,
        .excel-settings-compact span,
        .excel-settings-compact div {
          font-size: 11px !important;
        }
        .excel-settings-compact .chakra-stack,
        .excel-settings-compact .chakra-grid,
        .excel-settings-compact .chakra-flex:not(.checkbox-row) {
          gap: 6px !important;
        }
        .excel-settings-compact .checkbox-row {
          gap: 16px !important;
        }
        .excel-settings-compact [class*="FieldLabel"],
        .excel-settings-compact label {
          margin-bottom: 2px !important;
        }
        .excel-settings-compact button {
          height: 22px !important;
          font-size: 10px !important;
          padding-inline: 8px !important;
        }
        .excel-settings-compact button.color-picker-btn {
          height: 20px !important;
          width: 20px !important;
          min-width: 20px !important;
        }
      `,
        }}
      />

      {/* Settings Navigation Tabs */}
      <HStack
        mb={4}
        borderBottom="1px solid"
        borderColor="gray.200"
        pb={2}
        gap={4}
      >
        <Button
          size="xs"
          variant={activeTab === "basic" ? "solid" : "ghost"}
          colorPalette={activeTab === "basic" ? "brand" : "gray"}
          onClick={() => setActiveTab("basic")}
        >
          Basic Options
        </Button>
        <Button
          size="xs"
          variant={activeTab === "styling" ? "solid" : "ghost"}
          colorPalette={activeTab === "styling" ? "brand" : "gray"}
          onClick={() => setActiveTab("styling")}
        >
          Styling
        </Button>
        <Button
          size="xs"
          variant={activeTab === "formatting" ? "solid" : "ghost"}
          colorPalette={activeTab === "formatting" ? "brand" : "gray"}
          onClick={() => setActiveTab("formatting")}
        >
          Conditional Formatting ({rules.length})
        </Button>
      </HStack>

      {/* Tab: Basic Options */}
      {activeTab === "basic" && (
        <BasicOptionsTab
          options={options}
          sheetName={sheetName}
          colNames={colNames}
          updateOptions={updateOptions}
        />
      )}

      {/* Tab: Styling */}
      {activeTab === "styling" && (
        <StylingTab
          options={options}
          headerStyle={headerStyle}
          sheetHeaderStyle={sheetHeaderStyle}
          bodyStyle={bodyStyle}
          isSheetHeaderEnabled={isSheetHeaderEnabled}
          sheetName={sheetName}
          tableName={tableName}
          updateOptions={updateOptions}
          updateHeaderStyle={updateHeaderStyle}
          updateSheetHeaderStyle={updateSheetHeaderStyle}
          updateBodyStyle={updateBodyStyle}
          onClearBanding={handleClearBanding}
        />
      )}

      {/* Tab: Conditional Formatting */}
      {activeTab === "formatting" && (
        <ConditionalFormattingTab
          rules={rules}
          colNames={colNames}
          tableFields={tableFields}
          onAddRule={handleAddRule}
          onUpdateRule={handleUpdateRule}
          onUpdateRuleStyle={handleUpdateRuleStyle}
          onDeleteRule={handleDeleteRule}
        />
      )}
    </Box>
  );
}
