import { useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  CloseButton,
  Dialog,
  Field,
  Flex,
  Grid,
  Input,
  NativeSelect,
  Portal,
  Text,
} from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";
import {
  type ExcelConditionalFormat,
  type ExcelOptions,
  type FilenameDateFormat,
  type TableFieldInfo,
} from "@/types/connectors";

import ExcelSettings, { validateConditionalFormatRules } from "./ExcelSettings";

export type FileFormat = "csv" | "json" | "parquet" | "excel";

export interface TableExportSetting {
  output_file_name: string;
  target_folder: string;
  file_format: FileFormat;
  csv_delimiter?: string;
  csv_quote_char?: string;
  add_utc_timestamp: boolean;
  filename_date_format: FilenameDateFormat | null;
  notification_email_group_ids?: number[];
  excel_sheet_name?: string;
  excel_options?: ExcelOptions;
  excel_conditional_formats?: ExcelConditionalFormat[];
}

interface TableExportSettingsModalProps {
  open: boolean;
  onClose: () => void;
  tableName: string;
  settings: TableExportSetting;
  tableFields: Record<string, string | TableFieldInfo>;
  onSave: (_settings: TableExportSetting) => void;
  isSaving?: boolean;
  supportedFormats?: string[];
  selectedFields?: string[] | null;
}

const FILENAME_DATE_FORMAT_OPTIONS: Array<{
  label: string;
  value: FilenameDateFormat | "current";
}> = [
  { label: "UTC Timestamp", value: "current" },
  { label: "YYYY-MM-DD", value: "yyyy_mm_dd" },
  { label: "MM-DD-YYYY", value: "mm_dd_yyyy" },
  { label: "DD-MM-YYYY", value: "dd_mm_yyyy" },
];

const getCurrentTimestampPreview = (format: FilenameDateFormat | "current") => {
  const now = new Date();
  const year = String(now.getUTCFullYear());
  const month = String(now.getUTCMonth() + 1).padStart(2, "0");
  const day = String(now.getUTCDate()).padStart(2, "0");

  if (format === "yyyy_mm_dd") return `${year}_${month}_${day}`;
  if (format === "dd_mm_yyyy") return `${day}_${month}_${year}`;
  if (format === "mm_dd_yyyy") return `${month}_${day}_${year}`;

  const hours = String(now.getUTCHours()).padStart(2, "0");
  const minutes = String(now.getUTCMinutes()).padStart(2, "0");
  const seconds = String(now.getUTCSeconds()).padStart(2, "0");
  return `${year}${month}${day}T${hours}${minutes}${seconds}Z`;
};

const FILE_EXTENSIONS: Record<FileFormat, string> = {
  csv: "csv",
  json: "json",
  parquet: "parquet",
  excel: "xlsx",
};

const TableExportSettingsModal = ({
  open,
  onClose,
  tableName,
  settings,
  tableFields,
  onSave,
  isSaving = false,
  supportedFormats,
  selectedFields,
}: TableExportSettingsModalProps) => {
  const [prevOpen, setPrevOpen] = useState(open);
  const [localSettings, setLocalSettings] =
    useState<TableExportSetting>(settings);
  const [targetFolderError, setTargetFolderError] = useState("");

  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setLocalSettings(settings);
      setTargetFolderError("");
    }
  }

  const updateLocalSetting = (patch: Partial<TableExportSetting>) => {
    setLocalSettings((prev) => {
      const updated = { ...prev, ...patch };
      if (Object.prototype.hasOwnProperty.call(patch, "target_folder")) {
        setTargetFolderError("");
      }
      return updated;
    });
  };

  const handleTargetFolderChange = (value: string) => {
    updateLocalSetting({ target_folder: value });
  };

  const handleSave = () => {
    if (localSettings.file_format === "excel") {
      const errorMsg = validateConditionalFormatRules(
        localSettings.excel_conditional_formats || [],
        normalizedTableFields,
      );
      if (errorMsg) {
        toaster.error({
          title: "Incomplete Condition",
          description: errorMsg,
        });
        return;
      }
    }
    onSave(localSettings);
  };

  const previewFileName = (() => {
    const outputFileName = localSettings.output_file_name.trim() || tableName;
    const extension = FILE_EXTENSIONS[localSettings.file_format];
    if (!localSettings.add_utc_timestamp) {
      return `${outputFileName}.${extension}`;
    }

    const timestamp = getCurrentTimestampPreview(
      localSettings.filename_date_format ?? "current",
    );
    return `${outputFileName}_${timestamp}.${extension}`;
  })();

  // Normalize tableFields for ExcelSettings component.
  // If selectedFields are specified from "select and order fields", use only those in that exact order.
  const normalizedTableFields = (() => {
    const fieldsToUse =
      selectedFields && selectedFields.length > 0
        ? selectedFields.filter((f) => tableFields[f] !== undefined)
        : Object.keys(tableFields);

    return fieldsToUse.reduce<Record<string, string>>((acc, key) => {
      const val = tableFields[key];
      acc[key] = typeof val === "string" ? val : val?.data_type || "";
      return acc;
    }, {});
  })();

  return (
    <Dialog.Root lazyMount open={open} size="xl">
      <Portal>
        <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
        <Dialog.Positioner>
          <Dialog.Content
            borderRadius="xl"
            boxShadow="2xl"
            bg="white"
            overflow="hidden"
            maxH="92vh"
            height={localSettings.file_format === "excel" ? "85vh" : "auto"}
            display="flex"
            flexDirection="column"
            maxW={localSettings.file_format === "excel" ? "1080px" : "1000px"}
            width="95%"
          >
            <Dialog.Header
              bg="brand.50"
              borderBottomWidth="1px"
              borderColor="brand.100"
              p={3}
            >
              <Flex
                justifyContent="space-between"
                alignItems="center"
                width="100%"
              >
                <Dialog.Title color="brand.800" fontWeight="bold" fontSize="lg">
                  File Export Settings: {tableName}
                </Dialog.Title>
              </Flex>
            </Dialog.Header>
            <Dialog.Body p={3} overflowY="auto">
              <Flex width="100%" direction="column" gap={2}>
                <Grid templateColumns="1fr 1fr" gap={2}>
                  <Box>
                    <Field.Root gap={0} invalid={!!targetFolderError}>
                      <Field.Label
                        fontSize="xs"
                        fontWeight="semibold"
                        color="gray.600"
                        mb={0.5}
                      >
                        Target Folder
                      </Field.Label>
                      <Input
                        size="xs"
                        value={localSettings.target_folder}
                        onChange={(e) =>
                          handleTargetFolderChange(e.target.value)
                        }
                        placeholder="Target folder"
                      />
                      {targetFolderError && (
                        <Field.ErrorText fontSize="xs">
                          {targetFolderError}
                        </Field.ErrorText>
                      )}
                    </Field.Root>
                  </Box>

                  <Box>
                    <Field.Root gap={0}>
                      <Field.Label
                        fontSize="xs"
                        fontWeight="semibold"
                        color="gray.600"
                        mb={0.5}
                      >
                        Output Filename
                      </Field.Label>
                      <Input
                        size="xs"
                        value={localSettings.output_file_name}
                        onChange={(e) =>
                          updateLocalSetting({
                            output_file_name: e.target.value,
                          })
                        }
                        placeholder="Target file name"
                      />
                    </Field.Root>
                  </Box>
                </Grid>

                <Checkbox.Root
                  size="sm"
                  colorPalette="brand"
                  checked={localSettings.add_utc_timestamp}
                  onCheckedChange={(details) =>
                    updateLocalSetting({
                      add_utc_timestamp: !!details.checked,
                    })
                  }
                  width="fit-content"
                >
                  <Checkbox.HiddenInput />
                  <Checkbox.Control />
                  <Checkbox.Label
                    fontSize="xs"
                    fontWeight="semibold"
                    color="gray.700"
                  >
                    Add date to filename
                  </Checkbox.Label>
                </Checkbox.Root>

                <Grid templateColumns="1fr 1fr" gap={2}>
                  <Grid
                    templateColumns={
                      localSettings.add_utc_timestamp ? "1fr 1fr" : "1fr"
                    }
                    gap={2}
                  >
                    <Box>
                      <Field.Root gap={0}>
                        <Field.Label
                          fontSize="xs"
                          fontWeight="semibold"
                          color="gray.600"
                          mb={0.5}
                        >
                          File Type
                        </Field.Label>
                        <NativeSelect.Root size="xs">
                          <NativeSelect.Field
                            value={localSettings.file_format}
                            onChange={(e) =>
                              updateLocalSetting({
                                file_format: e.target.value as FileFormat,
                              })
                            }
                          >
                            {(supportedFormats && supportedFormats.length > 0
                              ? supportedFormats
                              : ["csv", "excel", "json", "parquet"]
                            ).map((format) => (
                              <option key={format} value={format}>
                                {format === "csv"
                                  ? "CSV"
                                  : format === "excel"
                                    ? "Excel"
                                    : format === "json"
                                      ? "JSON"
                                      : format === "parquet"
                                        ? "Parquet"
                                        : format.toUpperCase()}
                              </option>
                            ))}
                          </NativeSelect.Field>
                          <NativeSelect.Indicator />
                        </NativeSelect.Root>
                      </Field.Root>
                    </Box>

                    {localSettings.add_utc_timestamp && (
                      <Box>
                        <Field.Root gap={0}>
                          <Field.Label
                            fontSize="xs"
                            fontWeight="semibold"
                            color="gray.600"
                            mb={0.5}
                          >
                            Filename date format
                          </Field.Label>
                          <NativeSelect.Root size="xs">
                            <NativeSelect.Field
                              value={
                                localSettings.filename_date_format ?? "current"
                              }
                              onChange={(e) =>
                                updateLocalSetting({
                                  filename_date_format:
                                    e.target.value === "current"
                                      ? null
                                      : (e.target.value as FilenameDateFormat),
                                })
                              }
                            >
                              {FILENAME_DATE_FORMAT_OPTIONS.map(
                                ({ label, value }) => (
                                  <option key={value} value={value}>
                                    {label}
                                  </option>
                                ),
                              )}
                            </NativeSelect.Field>
                            <NativeSelect.Indicator />
                          </NativeSelect.Root>
                        </Field.Root>
                      </Box>
                    )}
                  </Grid>

                  <Box>
                    <Text
                      fontSize="xs"
                      fontWeight="semibold"
                      color="gray.600"
                      mb={0.5}
                    >
                      Preview
                    </Text>
                    <Flex
                      minH="32px"
                      alignItems="center"
                      px={2.5}
                      borderWidth="1px"
                      borderColor="gray.200"
                      borderRadius="md"
                      bg="gray.50"
                    >
                      <Text fontSize="xs" color="gray.700" truncate>
                        {previewFileName}
                      </Text>
                    </Flex>
                  </Box>
                </Grid>

                {localSettings.file_format === "csv" && (
                  <Flex gap={3}>
                    <Box flex={1}>
                      <Text
                        fontSize="xs"
                        fontWeight="semibold"
                        color="gray.600"
                        mb={0.5}
                      >
                        CSV Delimiter
                      </Text>
                      <Input
                        size="xs"
                        value={localSettings.csv_delimiter ?? ""}
                        onChange={(e) =>
                          updateLocalSetting({
                            csv_delimiter: e.target.value,
                          })
                        }
                        placeholder=","
                      />
                    </Box>
                    <Box flex={1}>
                      <Text
                        fontSize="xs"
                        fontWeight="semibold"
                        color="gray.600"
                        mb={0.5}
                      >
                        CSV Quote Char
                      </Text>
                      <Input
                        size="xs"
                        value={localSettings.csv_quote_char ?? ""}
                        onChange={(e) =>
                          updateLocalSetting({
                            csv_quote_char: e.target.value,
                          })
                        }
                        placeholder={'"'}
                      />
                    </Box>
                  </Flex>
                )}

                {localSettings.file_format === "excel" && (
                  <ExcelSettings
                    excelOptions={localSettings.excel_options}
                    excelSheetName={localSettings.excel_sheet_name}
                    conditionalFormats={localSettings.excel_conditional_formats}
                    tableFields={normalizedTableFields}
                    selectedFields={selectedFields}
                    tableName={tableName}
                    onChange={(patch) => updateLocalSetting(patch)}
                  />
                )}
              </Flex>
            </Dialog.Body>
            <Dialog.Footer
              bg="gray.50"
              borderTopWidth="1px"
              borderColor="gray.100"
              p={3}
              gap={3}
            >
              <Button
                variant="outline"
                onClick={onClose}
                px={6}
                borderRadius="full"
                disabled={isSaving}
              >
                Cancel
              </Button>
              <Button
                colorPalette="brand"
                onClick={handleSave}
                loading={isSaving}
                px={6}
                borderRadius="full"
              >
                Save
              </Button>
            </Dialog.Footer>
            <Dialog.CloseTrigger asChild>
              <CloseButton
                size="sm"
                onClick={onClose}
                position="absolute"
                right={4}
                top={4}
                disabled={isSaving}
              />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default TableExportSettingsModal;
