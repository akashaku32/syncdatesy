import React, { useMemo, useState } from "react";

import {
  Box,
  Button,
  CloseButton,
  Dialog,
  Field,
  Flex,
  Input,
  Portal,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";

import { MdOutlineSave } from "react-icons/md";

import {
  useFetchBatches,
  useRemoveTableFromBatch,
} from "@/queryOptions/connector/schema/useBatches";
import usePreviewPatternTables, {
  type PreviewPatternRequest,
} from "@/queryOptions/connector/usePreviewPatternTables";

interface MultipleMappingProps {
  formValues?: Record<string, unknown>;
  tableName?: string;
  selectedFiles?: string[];
  onSave: (_data: {
    tableName: string;
    prefix: string;
    selectedFiles: string[];
  }) => void;
  onCancel?: () => void;
  loading?: boolean;
  readOnly?: boolean;
  connectionId?: number;
  isSftp?: boolean;
  sourceType?: string;
}

const MultipleMapping: React.FC<MultipleMappingProps> = ({
  formValues,
  tableName: initialTableName = "",
  selectedFiles: initialSelectedFiles = [],
  onSave,
  onCancel,
  loading: saveLoading,
  readOnly = false,
  connectionId,
  isSftp: propIsSftp,
  sourceType: propSourceType,
}) => {
  const getDefaultPrefix = (fileType: string | undefined): string => {
    if (!fileType) return "";
    const ext = fileType.replace(/^\*\./, "").replace(/^\./, "").trim();
    return ext ? `*.${ext}` : "";
  };

  const [tableName, setTableName] = useState(initialTableName);
  const [prefix] = useState(
    (formValues?.multi_files_prefix as string) ||
      getDefaultPrefix(formValues?.file_type as string | undefined),
  );
  const isEditMode = !!connectionId;
  const [shouldFetchPreview, setShouldFetchPreview] = useState(
    () => isEditMode && !!prefix.trim(),
  );
  const [hasEverPreviewed, setHasEverPreviewed] = useState(
    () => isEditMode && !!prefix.trim(),
  );

  const isSftp = useMemo(() => {
    if (propIsSftp !== undefined) return propIsSftp;
    return !!(formValues?.sftp_host || formValues?.root_folder);
  }, [formValues, propIsSftp]);
  const sourceType = propSourceType || (isSftp ? "sftp" : "s3");
  const isGoogleDrive = sourceType === "googledrive";
  const isAdls = sourceType === "azuredatalakestorage" || sourceType === "adls";
  const sourceLabel = isSftp
    ? "SFTP"
    : isGoogleDrive
      ? "Google Drive"
      : isAdls
        ? "Azure Data Lake Storage"
        : "S3";

  const hasRequiredCreds = useMemo(() => {
    if (connectionId) return true;
    if (isGoogleDrive) {
      return !!(
        formValues?.root_folder ||
        formValues?.base_folder_path ||
        formValues?.folder_path ||
        formValues?.folder_id ||
        formValues?.folder_name ||
        formValues?.client_id ||
        formValues?.client_secret ||
        formValues?.service_account_json
      );
    }
    if (isAdls) {
      return !!(
        formValues?.account_name ||
        formValues?.storage_account_name ||
        formValues?.container_name ||
        formValues?.file_system ||
        formValues?.connection_string ||
        formValues?.sas_token
      );
    }
    if (isSftp) {
      return (
        !!formValues?.sftp_host &&
        !!formValues?.sftp_username &&
        !!formValues?.root_folder
      );
    }
    return (
      !!formValues?.s3_bucket &&
      !!formValues?.aws_access_key_id &&
      !!formValues?.aws_secret_access_key
    );
  }, [
    formValues?.s3_bucket,
    formValues?.aws_access_key_id,
    formValues?.aws_secret_access_key,
    formValues?.sftp_host,
    formValues?.sftp_username,
    formValues?.root_folder,
    formValues?.account_name,
    formValues?.storage_account_name,
    formValues?.container_name,
    formValues?.file_system,
    formValues?.connection_string,
    formValues?.sas_token,
    connectionId,
    isSftp,
    isGoogleDrive,
    isAdls,
  ]);

  const previewParams = useMemo(() => {
    if (!hasRequiredCreds || !prefix.trim() || !shouldFetchPreview) return null;
    if (isGoogleDrive) {
      return {
        ...formValues,
        base_folder_path: formValues?.base_folder_path as string | undefined,
        root_folder: formValues?.root_folder as string | undefined,
        file_type: formValues?.file_type as string | undefined,
        multi_files_prefix: prefix.trim(),
        include_subfolders: String(formValues?.include_subfolders || "false"),
        file_mapping_method: formValues?.file_mapping_method as
          | string
          | undefined,
        connection_id: connectionId,
        sourceType,
      } as unknown as PreviewPatternRequest;
    }
    if (isAdls) {
      return {
        ...formValues,
        account_name:
          formValues?.account_name || formValues?.storage_account_name,
        container_name: formValues?.container_name || formValues?.file_system,
        folder_name:
          formValues?.folder_name ||
          formValues?.root_folder ||
          formValues?.folder_path ||
          formValues?.base_folder_path,
        file_type: formValues?.file_type as string | undefined,
        multi_files_prefix: prefix.trim(),
        include_subfolders: String(formValues?.include_subfolders || "false"),
        file_mapping_method: formValues?.file_mapping_method as
          | string
          | undefined,
        connection_id: connectionId,
        sourceType,
      } as unknown as PreviewPatternRequest;
    }
    if (isSftp) {
      return {
        sftp_host: String(formValues?.sftp_host || "").trim(),
        sftp_port: formValues?.sftp_port,
        sftp_username: String(formValues?.sftp_username || "").trim(),
        sftp_password: formValues?.sftp_password,
        sftp_private_key: formValues?.sftp_private_key,
        sftp_passphrase: formValues?.sftp_passphrase,
        root_folder: String(formValues?.root_folder || "").trim(),
        base_folder_path: formValues?.base_folder_path as string | undefined,
        file_type: formValues?.file_type as string | undefined,
        multi_files_prefix: prefix.trim(),
        include_subfolders: String(formValues?.include_subfolders || "false"),
        file_mapping_method: formValues?.file_mapping_method as
          | string
          | undefined,
        connection_id: connectionId,
        isSftp: true,
        sourceType,
      } as unknown as PreviewPatternRequest;
    }
    return {
      s3_bucket: String(formValues?.s3_bucket || "").trim(),
      aws_access_key_id: String(formValues?.aws_access_key_id || "").trim(),
      aws_secret_access_key: String(
        formValues?.aws_secret_access_key || "",
      ).trim(),
      base_folder_path: formValues?.base_folder_path as string | undefined,
      file_type: formValues?.file_type as string | undefined,
      multi_files_prefix: prefix.trim(),
      include_subfolders: String(formValues?.include_subfolders || "false"),
      file_mapping_method: formValues?.file_mapping_method as
        | string
        | undefined,
      connection_id: connectionId,
      sourceType,
    } as PreviewPatternRequest;
  }, [
    hasRequiredCreds,
    isSftp,
    isGoogleDrive,
    formValues,
    prefix,
    shouldFetchPreview,
    connectionId,
    sourceType,
  ]);

  const { data: previewData, isLoading: isPreviewLoading } =
    usePreviewPatternTables(
      previewParams ?? ({} as PreviewPatternRequest),
      !!previewParams &&
        hasRequiredCreds &&
        !!prefix.trim() &&
        shouldFetchPreview,
    );

  const matchedTables = useMemo(() => {
    const results =
      previewData?.matched_tables ||
      previewData?.matched_files ||
      previewData?.tables ||
      previewData?.results ||
      previewData?.data ||
      (Array.isArray(previewData) ? previewData : null);

    if (results && Array.isArray(results) && results.length > 0) {
      return results
        .map((t) =>
          typeof t === "string" ? t : t.file_key || t.file_name || t.table,
        )
        .filter((name): name is string => !!name);
    }

    const nonMatched =
      previewData?.sample_non_matched_files || previewData?.non_matched_files;
    if (nonMatched && Array.isArray(nonMatched) && nonMatched.length > 0) {
      return nonMatched
        .map((t) =>
          typeof t === "string" ? t : t.file_key || t.file_name || t.table,
        )
        .filter((name): name is string => !!name);
    }

    if (!hasEverPreviewed) {
      return initialSelectedFiles;
    }

    return [];
  }, [previewData, initialSelectedFiles, hasEverPreviewed]);

  const { data: batchesData } = useFetchBatches(
    connectionId ?? 0,
    !!connectionId,
  );
  const { mutate: removeTableFromBatch } = useRemoveTableFromBatch(
    connectionId ?? 0,
  );

  const [showBatchWarningDialog, setShowBatchWarningDialog] = useState(false);
  const [affectedBatchTables, setAffectedBatchTables] = useState<
    { batchId: number; batchName: string; tableName: string }[]
  >([]);
  const [pendingSaveData, setPendingSaveData] = useState<{
    tableName: string;
    prefix: string;
    selectedFiles: string[];
  } | null>(null);

  const handleSave = () => {
    const saveData = {
      tableName,
      prefix,
      selectedFiles: matchedTables,
    };

    if (
      connectionId &&
      batchesData?.batches &&
      batchesData.batches.length > 0
    ) {
      const removedFiles = initialSelectedFiles.filter(
        (f) => !matchedTables.includes(f),
      );
      const isTableNameChanged =
        initialTableName && initialTableName !== tableName;

      const affectedBatchItems: {
        batchId: number;
        batchName: string;
        tableName: string;
      }[] = [];

      batchesData.batches.forEach((batch) => {
        batch.tables?.forEach((bt) => {
          const btName = bt.table_name?.toLowerCase();
          const isMatch =
            (initialTableName &&
              btName === initialTableName.toLowerCase() &&
              isTableNameChanged) ||
            removedFiles.some((rf) => rf.toLowerCase() === btName);

          if (isMatch) {
            if (
              !affectedBatchItems.some(
                (item) =>
                  item.batchId === batch.id &&
                  item.tableName.toLowerCase() === btName,
              )
            ) {
              affectedBatchItems.push({
                batchId: batch.id,
                batchName: batch.name,
                tableName: bt.table_name,
              });
            }
          }
        });
      });

      if (affectedBatchItems.length > 0) {
        setPendingSaveData(saveData);
        setAffectedBatchTables(affectedBatchItems);
        setShowBatchWarningDialog(true);
        return;
      }
    }

    onSave(saveData);
  };

  const handleConfirmSave = () => {
    if (affectedBatchTables.length > 0 && connectionId) {
      affectedBatchTables.forEach((item) => {
        removeTableFromBatch({
          batchId: item.batchId,
          tableName: item.tableName,
        });
      });
    }
    setShowBatchWarningDialog(false);
    if (pendingSaveData) {
      onSave(pendingSaveData);
      setPendingSaveData(null);
    }
  };

  return (
    <VStack align="stretch" gap={3} w="100%">
      {/* ---------- HEADER ---------- */}
      <Flex direction="column" align="center" gap={1} mt={8}>
        <Text fontSize="lg" fontWeight="semibold" textAlign="center">
          Map Multiple Files to Single Table
        </Text>
        <Text fontSize="sm" color="gray.600" textAlign="center">
          Configure table mapping for multiple files with matching structure
        </Text>
      </Flex>

      <Flex gap={6} align="stretch" h="450px" maxW="1300px" mx="auto" w="100%">
        {/* Left Panel - Configuration */}
        <Box
          flex="1"
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="lg"
          bg="white"
          overflow="hidden"
          minW="300px"
          boxShadow="sm"
        >
          <Flex
            align="center"
            justify="space-between"
            px={4}
            h="52px"
            borderBottomWidth={1}
            borderBottomColor="gray.200"
            bg="gray.50"
          >
            <Text fontWeight="semibold" fontSize="sm">
              Table Configuration
            </Text>
          </Flex>

          <VStack align="stretch" gap={4} px={4} py={4}>
            {/* ---------- TABLE NAME ---------- */}
            <Field.Root required>
              <Field.Label htmlFor="tableName">
                Destination Table Name
              </Field.Label>
              <Input
                id="tableName"
                name="tableName"
                placeholder="Enter table name"
                value={tableName}
                autoComplete="off"
                onChange={(e) => setTableName(e.target.value)}
                size="sm"
                readOnly={readOnly}
                disabled={readOnly}
                bg={readOnly ? "gray.200" : undefined}
                color={readOnly ? "gray.700" : undefined}
                cursor={readOnly ? "not-allowed" : undefined}
                opacity={readOnly ? 0.8 : 1}
              />
              <Field.HelperText>
                All selected files will be mapped to this table
              </Field.HelperText>

              {/* Preview Button - only shown when not read-only */}
              {!readOnly && (
                <Button
                  size="sm"
                  variant="outline"
                  colorPalette="brand"
                  mt={4}
                  onClick={() => {
                    if (tableName.trim() && prefix.trim()) {
                      setHasEverPreviewed(true);
                      setShouldFetchPreview(true);
                    }
                  }}
                  disabled={!tableName.trim() || !prefix.trim()}
                  w="fit-content"
                >
                  Preview
                </Button>
              )}
            </Field.Root>
          </VStack>
        </Box>

        {/* Right Panel - Matched Tables Preview */}
        <Box
          flex="1"
          borderWidth={1}
          borderColor="gray.300"
          borderRadius="lg"
          bg="white"
          overflow="hidden"
          minW="300px"
          boxShadow="sm"
          display="flex"
          flexDirection="column"
        >
          <Flex
            h="52px"
            px={5}
            borderBottomWidth={1}
            borderColor="gray.200"
            justify="space-between"
            align="center"
          >
            <Text fontWeight="semibold" fontSize="sm">
              Files
            </Text>
            {matchedTables.length > 0 && (
              <Text fontSize="sm" color="gray.600">
                {matchedTables.length} files found
              </Text>
            )}
          </Flex>

          <Box overflowY="auto" flex="1">
            {isPreviewLoading ? (
              <VStack gap={3} align="center" py={12}>
                <Spinner size="sm" color="brand.500" />
                <Text fontSize="sm" color="gray.500" fontWeight="medium"></Text>
              </VStack>
            ) : !hasRequiredCreds && matchedTables.length === 0 ? (
              <VStack gap={2} align="center" py={6} color="gray.500">
                <Text fontSize="sm" fontWeight="medium">
                  No files configured
                </Text>
                <Text fontSize="xs" textAlign="center">
                  {sourceLabel} credentials are not available in edit mode for
                  security. The saved file list is shown below.
                </Text>
              </VStack>
            ) : !hasRequiredCreds && matchedTables.length > 0 ? (
              <>
                <VStack
                  gap={2}
                  align="center"
                  py={2}
                  bg="blue.50"
                  borderRadius="md"
                  mb={2}
                >
                  <Text fontSize="xs" color="blue.700" textAlign="center">
                    📝 Edit Mode: Showing saved files. Preview not available
                    without {sourceLabel} credentials.
                  </Text>
                </VStack>
                {matchedTables.map((table, index) => (
                  <Flex
                    key={index}
                    h="52px"
                    px={4}
                    borderBottomWidth={1}
                    borderColor="gray.100"
                    align="center"
                    gap={2}
                    bg={index % 2 === 0 ? "white" : "gray.50"}
                    _hover={{ bg: "gray.100" }}
                  >
                    <Text fontSize="sm" color="gray.700" flex="1">
                      {table}
                    </Text>
                  </Flex>
                ))}
              </>
            ) : !hasEverPreviewed && matchedTables.length === 0 ? (
              <VStack gap={2} align="center" py={6} color="gray.500">
                <Text fontSize="sm" fontWeight="medium">
                  Preview matching files
                </Text>
                <Text fontSize="xs" textAlign="center">
                  Click the Preview button to see files matching the
                  configuration
                </Text>
              </VStack>
            ) : matchedTables.length > 0 ? (
              matchedTables.map((table, index) => (
                <Flex
                  key={index}
                  h="52px"
                  px={4}
                  borderBottomWidth={1}
                  borderColor="gray.100"
                  align="center"
                  gap={2}
                  bg={index % 2 === 0 ? "white" : "gray.50"}
                  _hover={{ bg: "gray.100" }}
                >
                  <Text fontSize="sm" color="gray.700" flex="1">
                    {table}
                  </Text>
                </Flex>
              ))
            ) : (
              <VStack gap={2} align="center" py={6} color="gray.500">
                <Text fontSize="sm" fontWeight="medium">
                  No files found
                </Text>
              </VStack>
            )}
          </Box>
        </Box>
      </Flex>

      {/* ---------- ACTIONS BELOW PANELS ---------- */}
      <Flex justify="flex-end" gap={3} maxW="1300px" mx="auto" w="100%" pb={4}>
        {onCancel && (
          <Button variant="outline" onClick={onCancel}>
            {readOnly ? "Close" : "Cancel"}
          </Button>
        )}
        {!readOnly && (
          <Button
            colorPalette="brand"
            onClick={handleSave}
            disabled={!tableName.trim() || matchedTables.length === 0}
            loading={saveLoading}
          >
            <MdOutlineSave />
            Save Mapping
          </Button>
        )}
      </Flex>

      {/* Batch Removal Confirmation Dialog */}
      <Dialog.Root
        lazyMount
        open={showBatchWarningDialog}
        role="alertdialog"
        onOpenChange={(e) => {
          if (!e.open) setShowBatchWarningDialog(false);
        }}
      >
        <Portal>
          <Dialog.Backdrop />
          <Dialog.Positioner>
            <Dialog.Content maxW="500px">
              <Dialog.Header>
                <Dialog.Title>Confirm removal</Dialog.Title>
              </Dialog.Header>
              <Dialog.Body>
                <VStack align="stretch" gap={3}>
                  <Text fontSize="sm">
                    This table is associated with a batch. Removing it will also
                    remove it from that batch. Do you want to proceed?
                  </Text>
                  {affectedBatchTables.length > 0 && (
                    <Box
                      bg="red.50"
                      p={3}
                      borderRadius="md"
                      borderWidth={1}
                      borderColor="red.200"
                    >
                      <Text
                        fontSize="xs"
                        fontWeight="semibold"
                        color="red.800"
                        mb={1}
                      >
                        Affected Batch Tables:
                      </Text>
                      <VStack align="stretch" gap={1}>
                        {affectedBatchTables.map((item, idx) => (
                          <Text key={idx} fontSize="xs" color="red.700">
                            • <strong>{item.tableName}</strong> in batch{" "}
                            <strong>{item.batchName}</strong>
                          </Text>
                        ))}
                      </VStack>
                    </Box>
                  )}
                </VStack>
              </Dialog.Body>
              <Dialog.Footer>
                <Dialog.ActionTrigger asChild>
                  <Button
                    variant="outline"
                    autoFocus
                    onClick={() => setShowBatchWarningDialog(false)}
                  >
                    Cancel
                  </Button>
                </Dialog.ActionTrigger>
                <Button colorPalette="brand" onClick={handleConfirmSave}>
                  Confirm removal
                </Button>
              </Dialog.Footer>
              <Dialog.CloseTrigger asChild>
                <CloseButton
                  size="sm"
                  onClick={() => setShowBatchWarningDialog(false)}
                />
              </Dialog.CloseTrigger>
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>
    </VStack>
  );
};

export default MultipleMapping;
