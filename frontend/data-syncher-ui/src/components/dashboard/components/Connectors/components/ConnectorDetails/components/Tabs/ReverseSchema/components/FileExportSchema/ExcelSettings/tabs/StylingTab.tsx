import { useState } from "react";

import { Button, Flex, HStack, VStack } from "@chakra-ui/react";

import { type StylingTabProps } from "../helpers";
import BodyStyleSection from "./styling/BodyStyleSection";
import ColumnHeadersStyleSection from "./styling/ColumnHeadersStyleSection";
import SheetHeaderStyleSection from "./styling/SheetHeaderStyleSection";

interface ExtendedStylingTabProps extends StylingTabProps {
  sheetName?: string;
  tableName?: string;
  onClearBanding: () => void;
}

export default function StylingTab({
  options,
  headerStyle,
  sheetHeaderStyle,
  bodyStyle,
  isSheetHeaderEnabled,
  sheetName,
  tableName,
  updateOptions,
  updateHeaderStyle,
  updateSheetHeaderStyle,
  updateBodyStyle,
  onClearBanding,
}: ExtendedStylingTabProps) {
  const [activeStylingSubTab, setActiveStylingSubTab] = useState<
    "sheet_header" | "column_headers" | "body"
  >("column_headers");

  return (
    <VStack gap={3} align="stretch">
      {/* Sub-tabs Navigation */}
      <Flex
        borderBottom="1px solid"
        borderColor="gray.200"
        pb={1.5}
        justifyContent="space-between"
        alignItems="center"
        gap={2}
      >
        <HStack gap={2}>
          <Button
            size="2xs"
            variant={
              activeStylingSubTab === "sheet_header" ? "solid" : "outline"
            }
            colorPalette={
              activeStylingSubTab === "sheet_header" ? "brand" : "gray"
            }
            onClick={() => setActiveStylingSubTab("sheet_header")}
          >
            Sheet Header
          </Button>
          <Button
            size="2xs"
            variant={
              activeStylingSubTab === "column_headers" ? "solid" : "outline"
            }
            colorPalette={
              activeStylingSubTab === "column_headers" ? "brand" : "gray"
            }
            onClick={() => setActiveStylingSubTab("column_headers")}
          >
            Column Headers
          </Button>
          <Button
            size="2xs"
            variant={activeStylingSubTab === "body" ? "solid" : "outline"}
            colorPalette={activeStylingSubTab === "body" ? "brand" : "gray"}
            onClick={() => setActiveStylingSubTab("body")}
          >
            Body Style
          </Button>
        </HStack>
      </Flex>

      {/* Sub-tab: Sheet Header */}
      {activeStylingSubTab === "sheet_header" && (
        <SheetHeaderStyleSection
          options={options}
          sheetHeaderStyle={sheetHeaderStyle}
          isSheetHeaderEnabled={isSheetHeaderEnabled}
          tableName={tableName}
          updateOptions={updateOptions}
          updateSheetHeaderStyle={updateSheetHeaderStyle}
        />
      )}

      {/* Sub-tab: Column Headers */}
      {activeStylingSubTab === "column_headers" && (
        <ColumnHeadersStyleSection
          headerStyle={headerStyle}
          sheetName={sheetName}
          updateHeaderStyle={updateHeaderStyle}
        />
      )}

      {/* Sub-tab: Body Style */}
      {activeStylingSubTab === "body" && (
        <BodyStyleSection
          bodyStyle={bodyStyle}
          updateBodyStyle={updateBodyStyle}
          onClearBanding={onClearBanding}
        />
      )}
    </VStack>
  );
}
