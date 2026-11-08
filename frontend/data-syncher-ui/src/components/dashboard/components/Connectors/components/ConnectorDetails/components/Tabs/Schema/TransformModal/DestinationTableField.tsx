import { useEffect, useState } from "react";

import { Badge, Box, Button, Flex, Input, Text } from "@chakra-ui/react";

import { FiGrid } from "react-icons/fi";

import { isDatabricksConnector } from "@/components/dashboard/helpers/helpers";
import { type DestinationNameSource } from "@/utils/snowflakeDestinationTableName";

type DestinationTableFieldProps = {
  destinationTableName: string;
  destinationConnectionName?: string | null;
  jsonTableName: string | null;
  source: DestinationNameSource;
  defaultDisplayName: string;
  isEditing: boolean;
  editValue: string;
  error: string | null;
  warnings: string[];
  existingSnowflakeTable: boolean;
  confirmUseExisting: boolean;
  readOnly?: boolean;
  onUseDefault: () => void;
  onStartEdit: () => void;
  onEditChange: (_value: string) => void;
  onCommitEdit: () => void;
  onCancelEdit: () => void;
  onConfirmUseExisting: () => void;
  onChooseAnotherName: () => void;
};

const tagLabel = (source: DestinationNameSource) => {
  if (source === "JSON") return "From JSON";
  if (source === "MANUAL") return "Edited";
  return "Default";
};

const DestinationTableField = ({
  destinationTableName,
  destinationConnectionName,
  jsonTableName,
  source,
  defaultDisplayName,
  isEditing,
  editValue,
  error,
  warnings,
  existingSnowflakeTable,
  confirmUseExisting,
  readOnly,
  onUseDefault,
  onStartEdit,
  onEditChange,
  onCommitEdit,
  onCancelEdit,
  onConfirmUseExisting,
  onChooseAnotherName,
}: DestinationTableFieldProps) => {
  const [copied, setCopied] = useState(false);
  const prefix = destinationConnectionName?.trim();
  const isDatabricks = isDatabricksConnector(
    destinationConnectionName ?? undefined,
  );
  const destinationLabel = isDatabricks
    ? "Unity Catalog table name"
    : "Destination table in Snowflake";
  const existingTableLabel = isDatabricks ? "Databricks" : "Snowflake";
  const subParts: string[] = [];
  if (jsonTableName) subParts.push(`JSON value: ${jsonTableName}`);
  if (
    defaultDisplayName &&
    defaultDisplayName.toUpperCase() !== destinationTableName.toUpperCase()
  ) {
    subParts.push(`default would be ${defaultDisplayName}`);
  }

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copyName = async () => {
    if (!destinationTableName) return;
    try {
      await navigator.clipboard.writeText(destinationTableName);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Box>
      <Box
        data-testid="destination-banner"
        position="sticky"
        top={0}
        zIndex={2}
        style={{ position: "sticky", top: 0, zIndex: 2 }}
        bg="purple.50"
        borderLeftWidth="5px"
        borderLeftColor="brand.500"
        borderRadius="md"
        px={3}
        py={3}
      >
        <Flex gap={3} align="flex-start">
          <Flex
            boxSize="36px"
            align="center"
            justify="center"
            bg="white"
            borderRadius="md"
            color="brand.800"
            flexShrink={0}
            aria-hidden
          >
            <FiGrid />
          </Flex>
          <Box flex="1" minW={0}>
            <Text
              fontSize="xs"
              fontWeight="semibold"
              letterSpacing="0.04em"
              textTransform="uppercase"
              color="gray.500"
            >
              {destinationLabel}
            </Text>
            <Flex gap={2} align="center" wrap="wrap" mt={1}>
              {prefix && (
                <Text fontSize="sm" color="gray.500">
                  {prefix} ›
                </Text>
              )}
              {isEditing ? (
                <Flex gap={2} align="center" wrap="wrap">
                  <Input
                    size="sm"
                    aria-label="Destination table name"
                    value={editValue}
                    onChange={(e) => onEditChange(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") onCommitEdit();
                      if (e.key === "Escape") onCancelEdit();
                    }}
                    autoFocus
                    maxW="280px"
                  />
                  <Button size="xs" colorPalette="brand" onClick={onCommitEdit}>
                    Apply
                  </Button>
                  <Button size="xs" variant="ghost" onClick={onCancelEdit}>
                    Cancel
                  </Button>
                </Flex>
              ) : (
                <Text fontSize="18px" fontWeight="medium" color="brand.800">
                  {destinationTableName || "—"}
                </Text>
              )}
              {!isEditing && (
                <Badge size="sm" variant="subtle" colorPalette="brand">
                  {tagLabel(source)}
                </Badge>
              )}
            </Flex>
            {subParts.length > 0 && (
              <Text fontSize="xs" color="gray.500" mt={1}>
                {subParts.join(" · ")}
              </Text>
            )}
          </Box>
          {!isEditing && (
            <Flex gap={1} align="center" flexShrink={0} wrap="wrap">
              <Button
                size="xs"
                variant="ghost"
                aria-label={copied ? "Copied" : "Copy destination table name"}
                onClick={() => void copyName()}
              >
                {copied ? "Copied" : "Copy"}
              </Button>
              {!readOnly && (
                <>
                  <Button size="xs" variant="ghost" onClick={onStartEdit}>
                    Edit
                  </Button>
                  <Button size="xs" variant="ghost" onClick={onUseDefault}>
                    Use default name
                  </Button>
                </>
              )}
            </Flex>
          )}
        </Flex>
      </Box>
      {warnings.map((w) => (
        <Text key={w} fontSize="xs" color="orange.600" mt={1}>
          {w}
        </Text>
      ))}
      {error && (
        <Text fontSize="xs" color="red.600" mt={1}>
          {error}
        </Text>
      )}
      {existingSnowflakeTable && !confirmUseExisting && (
        <Box
          mt={2}
          p={2}
          borderWidth={1}
          borderRadius="md"
          borderColor="orange.200"
        >
          <Text fontSize="xs" mb={2}>
            A {existingTableLabel} table named {destinationTableName} already
            exists and was not created by this mapping. Use the existing table
            or choose another name.
          </Text>
          <Flex gap={2}>
            <Button
              size="xs"
              colorPalette="brand"
              onClick={onConfirmUseExisting}
            >
              Use existing table
            </Button>
            <Button size="xs" variant="outline" onClick={onChooseAnotherName}>
              Choose another name
            </Button>
          </Flex>
        </Box>
      )}
    </Box>
  );
};

export default DestinationTableField;
