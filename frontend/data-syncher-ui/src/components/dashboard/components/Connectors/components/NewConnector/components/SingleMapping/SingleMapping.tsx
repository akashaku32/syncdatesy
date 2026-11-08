import React, { useMemo, useRef, useState } from "react";

import {
  Box,
  Button,
  Checkbox,
  CloseButton,
  Dialog,
  Flex,
  Input,
  InputGroup,
  Portal,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";

import { FiInbox, FiSearch } from "react-icons/fi";
import { MdOutlineSave } from "react-icons/md";

import {
  useFetchBatches,
  useRemoveTableFromBatch,
} from "@/queryOptions/connector/schema/useBatches";
import { type S3FileItem } from "@/queryOptions/connector/types/connector.d";
import useFetchS3Files, {
  type S3ListFilesRequest,
} from "@/queryOptions/connector/useFetchS3Files";

export type Mapping = {
  fileName: string;
  tableName: string;
  isSelected?: boolean;
  /** True when the backend indicates this mapping is already active and should be locked. */
  alreadyMapped?: boolean;
};

interface SingleMappingProps {
  formValues: Record<string, string>;
  mappings: Mapping[];
  onCancel: () => void;
  onSaveMappings: (_mappings: Mapping[]) => void;
  loading?: boolean;
  readOnly?: boolean;
  connectionId?: number;
  isSftp?: boolean;
  sourceType?: string;
}

const extractTableName = (fileName: string) =>
  fileName.replace(/\.[^/.]+$/, "").toLowerCase();

const SingleMapping: React.FC<SingleMappingProps> = ({
  formValues,
  mappings,
  onCancel,
  onSaveMappings,
  loading,
  readOnly = false,
  connectionId,
  isSftp: propIsSftp,
  sourceType: propSourceType,
}) => {
  const [localMappings, setLocalMappings] = useState<Mapping[]>(() => {
    // Initialize with props; scan will reconcile this later
    return mappings.map((m) => ({
      ...m,
      isSelected: m.isSelected ?? true,
    }));
  });
  const localMappingsRef = useRef<Mapping[]>(localMappings);
  const [_selectedFileName, setSelectedFileName] = useState<string | null>(
    null,
  );

  const isSftp = useMemo(() => {
    if (propIsSftp !== undefined) return propIsSftp;
    return !!(formValues?.sftp_host || formValues?.root_folder);
  }, [formValues, propIsSftp]);
  const sourceType = propSourceType || (isSftp ? "sftp" : "s3");
  const isGoogleDrive = sourceType === "googledrive";
  const isAdls = sourceType === "azuredatalakestorage" || sourceType === "adls";

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
  }, [formValues, connectionId, isSftp, isGoogleDrive, isAdls]);

  const s3Params = useMemo(() => {
    if (!hasRequiredCreds) return null;
    if (isGoogleDrive) {
      return {
        ...formValues,
        base_folder_path: formValues.base_folder_path || undefined,
        root_folder: formValues.root_folder || undefined,
        file_type: formValues.file_type || undefined,
        include_subfolders: formValues.include_subfolders || "false",
        file_mapping_method: formValues.file_mapping_method || undefined,
        connection_id: connectionId,
        sourceType,
      } as unknown as S3ListFilesRequest;
    }
    if (isAdls) {
      return {
        ...formValues,
        account_name:
          formValues.account_name || formValues.storage_account_name,
        container_name: formValues.container_name || formValues.file_system,
        folder_name:
          formValues.folder_name ||
          formValues.root_folder ||
          formValues.folder_path ||
          formValues.base_folder_path,
        file_type: formValues.file_type || undefined,
        include_subfolders: formValues.include_subfolders || "false",
        file_mapping_method: formValues.file_mapping_method || undefined,
        connection_id: connectionId,
        sourceType,
      } as unknown as S3ListFilesRequest;
    }
    if (isSftp) {
      return {
        sftp_host: (formValues.sftp_host || "").trim(),
        sftp_port: formValues.sftp_port,
        sftp_username: (formValues.sftp_username || "").trim(),
        sftp_password: formValues.sftp_password,
        sftp_private_key: formValues.sftp_private_key,
        sftp_passphrase: formValues.sftp_passphrase,
        root_folder: (formValues.root_folder || "").trim(),
        base_folder_path: formValues.base_folder_path || undefined,
        file_type: formValues.file_type || undefined,
        include_subfolders: formValues.include_subfolders || "false",
        file_mapping_method: formValues.file_mapping_method || undefined,
        connection_id: connectionId,
        isSftp: true,
        sourceType,
      } as unknown as S3ListFilesRequest;
    }
    return {
      s3_bucket: (formValues.s3_bucket || "").trim(),
      aws_access_key_id: (formValues.aws_access_key_id || "").trim(),
      aws_secret_access_key: (formValues.aws_secret_access_key || "").trim(),
      base_folder_path: formValues.base_folder_path || undefined,
      file_type: formValues.file_type || undefined,
      include_subfolders: formValues.include_subfolders || "false",
      file_mapping_method: formValues.file_mapping_method || undefined,
      connection_id: connectionId,
      sourceType,
    } as S3ListFilesRequest;
  }, [
    hasRequiredCreds,
    isSftp,
    isGoogleDrive,
    isAdls,
    formValues.sftp_host,
    formValues.sftp_port,
    formValues.sftp_username,
    formValues.sftp_password,
    formValues.sftp_private_key,
    formValues.sftp_passphrase,
    formValues.root_folder,
    formValues.s3_bucket,
    formValues.aws_access_key_id,
    formValues.aws_secret_access_key,
    formValues.base_folder_path,
    formValues.file_type,
    formValues.include_subfolders,
    formValues.file_mapping_method,
    connectionId,
    sourceType,
    formValues,
  ]);

  const { data: s3Files, isPending: isS3Loading } = useFetchS3Files(
    s3Params ?? ({} as S3ListFilesRequest),
    !!s3Params && hasRequiredCreds,
  );

  // Helper to extract tables from S3 response
  const s3TableList = useMemo(() => {
    if (!s3Files) return [];
    if (Array.isArray(s3Files)) return s3Files;
    if (s3Files.tables && Array.isArray(s3Files.tables)) return s3Files.tables;
    if (s3Files.files && Array.isArray(s3Files.files)) return s3Files.files;
    const rawRes = s3Files as unknown as Record<string, unknown>;
    if (rawRes?.result) {
      const res = rawRes.result as Record<string, unknown>;
      if (Array.isArray(res)) return res as S3FileItem[];
      if (Array.isArray(res?.tables)) return res.tables as S3FileItem[];
      if (Array.isArray(res?.files)) return res.files as S3FileItem[];
    }
    if (rawRes?.data) {
      const dataObj = rawRes.data as Record<string, unknown>;
      if (Array.isArray(dataObj)) return dataObj as S3FileItem[];
      if (Array.isArray(dataObj?.tables)) return dataObj.tables as S3FileItem[];
      if (Array.isArray(dataObj?.files)) return dataObj.files as S3FileItem[];
    }
    return [];
  }, [s3Files]);

  const [searchFiles, setSearchFiles] = useState("");
  const [searchMappings, setSearchMappings] = useState("");

  // 1. Initial selection
  React.useEffect(() => {
    localMappingsRef.current = localMappings;
  }, [localMappings]);

  React.useEffect(() => {
    if (mappings.length > 0 && !_selectedFileName) {
      setSelectedFileName(mappings[0].fileName);
    }
  }, [mappings, _selectedFileName]);

  // 2. Reconcile state with S3 Scan results: Strictly only show files found in S3
  React.useEffect(() => {
    if (!s3Files) return;

    if (s3TableList.length === 0) {
      setLocalMappings([]);
      setSelectedFileName(null);
      return;
    }

    setLocalMappings(() => {
      // Build the current list strictly based on what S3 API returned
      const next = s3TableList.map((t) => {
        const fileName = (t.file_key ||
          t.relative_path ||
          t.file_name ||
          t.table) as string;
        const suggestedTableName = t.table || extractTableName(fileName);

        // Check if this file was in the initial mappings (saved configuration)
        const savedMapping = mappings.find(
          (m) =>
            m.fileName === fileName ||
            (t.file_key && m.fileName === t.file_key) ||
            (t.table && m.fileName === t.table) ||
            (t.relative_path && m.fileName === t.relative_path),
        );

        const isLocked = !!t.table_name_locked;
        const savedTableName = savedMapping?.tableName?.trim();

        // Default new unmapped tables to unselected.
        const isSelected =
          savedMapping !== undefined
            ? savedMapping.isSelected !== false
            : false;

        return {
          fileName,
          tableName:
            (isLocked ? t.mapped_table || t.locked_table_name : null) ||
            savedTableName ||
            suggestedTableName,
          isSelected,
          alreadyMapped: isLocked,
        };
      });
      localMappingsRef.current = next;
      return next;
    });

    setSelectedFileName((prev) => {
      if (prev) return prev;
      const firstName = s3TableList.find(
        (t) => t.file_key || t.relative_path || t.file_name || t.table,
      );
      return (
        ((firstName?.file_key ||
          firstName?.relative_path ||
          firstName?.file_name ||
          firstName?.table) as string) || null
      );
    });
  }, [s3Files, s3TableList, mappings]);

  const filteredFiles = useMemo(() => {
    if (!searchFiles.trim()) return localMappings;
    return localMappings.filter((m) =>
      m.fileName.toLowerCase().includes(searchFiles.toLowerCase()),
    );
  }, [localMappings, searchFiles]);

  const filteredMappings = useMemo(() => {
    const selected = localMappings.filter((m) => m.isSelected);
    if (!searchMappings.trim()) return selected;
    return selected.filter(
      (m) =>
        m.fileName.toLowerCase().includes(searchMappings.toLowerCase()) ||
        m.tableName.toLowerCase().includes(searchMappings.toLowerCase()),
    );
  }, [localMappings, searchMappings]);

  const toggleFileSelection = (fileName: string, checked: boolean) => {
    if (readOnly) return;
    setLocalMappings((prev) => {
      const next = prev.map((m) =>
        m.fileName === fileName ? { ...m, isSelected: checked } : m,
      );
      localMappingsRef.current = next;
      return next;
    });
  };

  const updateTableName = (fileName: string, tableName: string) => {
    setLocalMappings((prev) => {
      const next = prev.map((m) =>
        m.fileName === fileName ? { ...m, tableName } : m,
      );
      localMappingsRef.current = next;
      return next;
    });
  };

  const { data: batchesData } = useFetchBatches(
    connectionId ?? 0,
    !!connectionId,
  );
  const { mutate: removeTableFromBatch } = useRemoveTableFromBatch(
    connectionId ?? 0,
  );

  const initialMappedFilesRef = useRef<Set<string>>(new Set());

  React.useEffect(() => {
    if (mappings && mappings.length > 0) {
      const initialFiles = new Set(
        mappings.filter((m) => m.isSelected !== false).map((m) => m.fileName),
      );
      initialMappedFilesRef.current = initialFiles;
    }
  }, [mappings]);

  const [showBatchWarningDialog, setShowBatchWarningDialog] = useState(false);
  const [affectedBatchTables, setAffectedBatchTables] = useState<
    { batchId: number; batchName: string; tableName: string }[]
  >([]);
  const [pendingSaveMappings, setPendingSaveMappings] = useState<
    Mapping[] | null
  >(null);

  const handleSave = () => {
    const selectedMappings = localMappingsRef.current.filter(
      (m) => m.isSelected,
    );

    // Check if any previously mapped file/table is being removed
    if (
      connectionId &&
      batchesData?.batches &&
      batchesData.batches.length > 0
    ) {
      const removedMappings = localMappingsRef.current.filter((m) => {
        const wasSelectedInitially =
          m.alreadyMapped ||
          initialMappedFilesRef.current.has(m.fileName) ||
          mappings.some(
            (initM) =>
              initM.fileName === m.fileName && initM.isSelected !== false,
          );
        return wasSelectedInitially && !m.isSelected;
      });

      const affectedBatchItems: {
        batchId: number;
        batchName: string;
        tableName: string;
      }[] = [];

      removedMappings.forEach((removedItem) => {
        const targetTableNames = [
          removedItem.tableName?.toLowerCase(),
          removedItem.fileName?.toLowerCase(),
        ].filter(Boolean);

        batchesData.batches.forEach((batch) => {
          batch.tables?.forEach((bt) => {
            const btName = bt.table_name?.toLowerCase();
            if (btName && targetTableNames.includes(btName)) {
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
      });

      if (affectedBatchItems.length > 0) {
        setPendingSaveMappings(selectedMappings);
        setAffectedBatchTables(affectedBatchItems);
        setShowBatchWarningDialog(true);
        return;
      }
    }

    onSaveMappings(selectedMappings);
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
    if (pendingSaveMappings) {
      onSaveMappings(pendingSaveMappings);
      setPendingSaveMappings(null);
    }
  };

  const isSaveDisabled =
    loading ||
    isS3Loading ||
    localMappings.length === 0 ||
    !localMappings.some((m) => m.isSelected && m.tableName.trim().length > 0);

  return (
    <VStack align="stretch" gap={4} p={4}>
      <Flex direction="column" align="center" gap={1} mt={2}>
        <Text fontSize="lg" fontWeight="semibold" textAlign="center">
          Map Files to Tables
        </Text>
      </Flex>

      {/* Grid Layout */}
      <Flex gap={4} h="450px" maxW="1300px" mx="auto" w="100%" mt={4}>
        {/* LEFT PANEL - Source Files */}
        <Flex direction="column" flex="1" minW={0} gap={3}>
          {/* Search for Source Files */}
          <InputGroup startElement={<FiSearch color="gray.500" />} w="100%">
            <Input
              size="sm"
              placeholder="Search source files..."
              value={searchFiles}
              autoComplete="off"
              onChange={(e) => setSearchFiles(e.target.value)}
              bg="white"
            />
          </InputGroup>

          {/* Source Files List */}
          <Box
            flex="1"
            borderWidth={1}
            borderColor="gray.300"
            borderRadius="lg"
            bg="white"
            overflow="hidden"
            boxShadow="sm"
          >
            <Flex
              align="center"
              justify="space-between"
              px={4}
              h="44px"
              borderBottomWidth={1}
              borderBottomColor="gray.200"
              bg="gray.50"
            >
              <Text fontWeight="semibold" fontSize="sm">
                Source Files
              </Text>
              <Flex align="center" gap={2} minW="60px" justify="center" />
            </Flex>

            <Box overflowY="auto" h="calc(100% - 44px)">
              {isS3Loading ? (
                <VStack gap={2} align="center" py={6} color="gray.500">
                  <Spinner size="sm" />
                  <Text fontSize="sm">Loading files...</Text>
                </VStack>
              ) : !hasRequiredCreds ? (
                <VStack gap={2} align="center" py={6} color="gray.500">
                  <Text fontSize="sm" fontWeight="medium">
                    Provide{" "}
                    {isSftp
                      ? "SFTP root folder and credentials"
                      : isGoogleDrive
                        ? "Google Drive folder details"
                        : "S3 bucket and credentials"}{" "}
                    to load files
                  </Text>
                </VStack>
              ) : filteredFiles.length === 0 ? (
                <VStack gap={2} align="center" py={6} color="gray.500">
                  <FiInbox size={20} />
                  <Text fontSize="sm" fontWeight="medium">
                    No files available
                  </Text>
                </VStack>
              ) : (
                filteredFiles.map((m) => {
                  return (
                    <Flex
                      key={m.fileName}
                      align="center"
                      justify="space-between"
                      h="44px"
                      px={4}
                      cursor={readOnly ? "default" : "pointer"}
                      bg="white"
                      borderBottomWidth={1}
                      borderBottomColor="gray.100"
                      _hover={!readOnly ? { bg: "gray.100" } : undefined}
                      onClick={() =>
                        !readOnly &&
                        toggleFileSelection(m.fileName, !m.isSelected)
                      }
                    >
                      <Text
                        fontSize="sm"
                        fontWeight="medium"
                        color="gray.900"
                        truncate
                        flex="1"
                        minW={0}
                        mr={2}
                        title={m.fileName}
                      >
                        {m.fileName}
                      </Text>
                      <Box onClick={(e) => e.stopPropagation()}>
                        <Checkbox.Root
                          colorPalette="brand"
                          variant="solid"
                          checked={m.isSelected}
                          disabled={readOnly}
                          onCheckedChange={({ checked }) =>
                            !readOnly &&
                            toggleFileSelection(m.fileName, checked === true)
                          }
                        >
                          <Checkbox.HiddenInput />
                          <Checkbox.Control
                            cursor={readOnly ? "not-allowed" : "pointer"}
                          />
                        </Checkbox.Root>
                      </Box>
                    </Flex>
                  );
                })
              )}
            </Box>
          </Box>
        </Flex>

        {/* RIGHT PANEL - Table Mapping */}
        <Flex direction="column" flex="1" minW={0} gap={3}>
          {/* Search for Mapped Tables */}
          <InputGroup startElement={<FiSearch color="gray.500" />} w="100%">
            <Input
              size="sm"
              placeholder="Search mapped tables..."
              value={searchMappings}
              autoComplete="off"
              onChange={(e) => setSearchMappings(e.target.value)}
              bg="white"
            />
          </InputGroup>

          {/* Table Mapping List */}
          <Box
            flex="1"
            borderWidth={1}
            borderColor="gray.300"
            borderRadius="lg"
            bg="white"
            overflow="hidden"
            boxShadow="sm"
          >
            <Flex
              align="center"
              justify="space-between"
              px={4}
              h="44px"
              borderBottomWidth={1}
              borderBottomColor="gray.200"
              bg="gray.50"
            >
              <Text fontWeight="semibold" fontSize="sm">
                Table Mapping
              </Text>
            </Flex>

            <Box overflowY="auto" h="calc(100% - 44px)">
              {isS3Loading ? (
                <VStack gap={2} align="center" py={6} color="gray.500">
                  <Spinner size="sm" />
                  <Text fontSize="sm">Loading files...</Text>
                </VStack>
              ) : !hasRequiredCreds ? (
                <VStack gap={2} align="center" py={6} color="gray.500">
                  <Text fontSize="sm" fontWeight="medium">
                    Provide{" "}
                    {isSftp
                      ? "SFTP root folder and credentials"
                      : isGoogleDrive
                        ? "Google Drive folder details"
                        : "S3 bucket and credentials"}{" "}
                    to load files
                  </Text>
                </VStack>
              ) : filteredMappings.length === 0 ? (
                <VStack gap={2} align="center" py={6} color="gray.500">
                  <Text fontSize="sm" fontWeight="medium">
                    Select files from the left to configure table mapping
                  </Text>
                </VStack>
              ) : (
                filteredMappings.map((mapping) => {
                  const isLocked = !!mapping.alreadyMapped;
                  return (
                    <Flex
                      key={mapping.fileName}
                      align="center"
                      justify="space-between"
                      h="44px"
                      px={4}
                      bg={isLocked ? "gray.50" : "white"}
                      borderBottomWidth={1}
                      borderBottomColor="gray.100"
                    >
                      <Text
                        fontSize="sm"
                        fontWeight="medium"
                        truncate
                        flex="1"
                        minW={0}
                        mr={3}
                        color={isLocked ? "gray.500" : "gray.900"}
                        title={mapping.fileName}
                      >
                        {mapping.fileName}
                      </Text>

                      <Input
                        size="sm"
                        placeholder="Enter table name"
                        value={mapping.tableName}
                        autoComplete="off"
                        onChange={(e) =>
                          !isLocked &&
                          updateTableName(mapping.fileName, e.target.value)
                        }
                        readOnly={isLocked || readOnly}
                        disabled={isLocked || readOnly}
                        bg={isLocked || readOnly ? "gray.100" : undefined}
                        color={isLocked || readOnly ? "gray.500" : undefined}
                        cursor={
                          isLocked || readOnly ? "not-allowed" : undefined
                        }
                        borderColor={
                          isLocked || readOnly ? "gray.100" : undefined
                        }
                        w="240px"
                        flexShrink={0}
                        h="32px"
                        fontSize="sm"
                      />
                    </Flex>
                  );
                })
              )}
            </Box>
          </Box>
        </Flex>
      </Flex>

      {/* Save/Cancel Buttons - Below both panels */}
      <Flex justify="flex-end" gap={3} maxW="1300px" mx="auto" w="100%">
        <Button variant="outline" onClick={onCancel}>
          {readOnly ? "Close" : "Cancel"}
        </Button>
        {!readOnly && (
          <Button
            colorPalette="brand"
            onClick={handleSave}
            loading={loading}
            disabled={isSaveDisabled}
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

export default SingleMapping;
