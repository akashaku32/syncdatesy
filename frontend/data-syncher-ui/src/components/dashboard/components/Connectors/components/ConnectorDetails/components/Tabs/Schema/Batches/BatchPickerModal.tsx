import { useState } from "react";

import {
  Box,
  Button,
  CloseButton,
  Dialog,
  Flex,
  Portal,
  Text,
} from "@chakra-ui/react";

import { GoPlus } from "react-icons/go";

import { AxiosError } from "axios";

import { toaster } from "@/components/ui/toaster";
import { useAddTablesToBatch } from "@/queryOptions/connector/schema/useBatches";
import {
  type AssignTablesErrorResponse,
  type MigrationBatch,
} from "@/types/connectors";

import NewBatchModal from "./NewBatchModal";
import { frequencyLabel } from "./scheduleOptions";

interface BatchPickerModalProps {
  open: boolean;
  onClose: () => void;
  connectionId: number;
  tables: string[];
  batches: MigrationBatch[];
}

const BatchPickerModal = ({
  open,
  onClose,
  connectionId,
  tables,
  batches,
}: BatchPickerModalProps) => {
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const { mutate: addTables, isPending } = useAddTablesToBatch(connectionId);

  const handleAssign = () => {
    if (!selectedBatchId || tables.length === 0) return;

    addTables(
      { batchId: selectedBatchId, payload: { tables } },
      {
        onSuccess: () => {
          toaster.success({
            title:
              tables.length === 1
                ? `Moved ${tables[0]}`
                : `Moved ${tables.length} tables`,
          });
          onClose();
        },
        onError: (err: unknown) => {
          const axiosErr = err as AxiosError<AssignTablesErrorResponse>;
          const conflicts = axiosErr?.response?.data?.conflicts;
          if (axiosErr?.response?.status === 409 && conflicts?.length) {
            toaster.error({
              title: "Some tables already belong to a batch",
              description: conflicts
                .map((c) => `${c.table_name} → ${c.batch_name}`)
                .join(", "),
            });
            return;
          }
          toaster.error({ title: "Could not move tables" });
        },
      },
    );
  };

  return (
    <>
      <Dialog.Root
        lazyMount
        open={open && !isCreateOpen}
        size="md"
        onOpenChange={(e) => {
          if (e.open) setSelectedBatchId(batches[0]?.id ?? null);
        }}
      >
        <Portal>
          <Dialog.Backdrop />
          <Dialog.Positioner>
            <Dialog.Content>
              <Dialog.Header>
                <Dialog.Title>
                  {tables.length === 1
                    ? `Move "${tables[0]}" to batch`
                    : `Move ${tables.length} tables to batch`}
                </Dialog.Title>
              </Dialog.Header>
              <Dialog.Body>
                <Flex direction="column" gap={2}>
                  {batches.length === 0 && (
                    <Text color="gray.600" fontSize="sm">
                      No batches yet. Create one to get started.
                    </Text>
                  )}

                  {batches.map((batch) => {
                    const isSelected = selectedBatchId === batch.id;
                    return (
                      <Box
                        key={batch.id}
                        as="button"
                        onClick={() => setSelectedBatchId(batch.id)}
                        textAlign="left"
                        px={3}
                        py={2}
                        borderRadius="md"
                        borderWidth={1}
                        borderColor={isSelected ? "brand.500" : "gray.300"}
                        bgColor={isSelected ? "brand.50" : "white"}
                        _hover={{ borderColor: "brand.500" }}
                        cursor="pointer"
                      >
                        <Flex
                          justifyContent="space-between"
                          alignItems="center"
                          gap={3}
                        >
                          <Flex direction="column" gap={0.5}>
                            <Text fontSize="sm" fontWeight="semibold">
                              {batch.name}
                            </Text>
                            <Text fontSize="xs" color="gray.600">
                              {frequencyLabel(batch.time_frequency)} ·{" "}
                              {batch.execution_order} · {batch.table_count}{" "}
                              tables
                            </Text>
                          </Flex>
                          {batch.status === "paused" && (
                            <Text
                              fontSize="xs"
                              color="orange.600"
                              fontWeight="semibold"
                            >
                              Paused
                            </Text>
                          )}
                        </Flex>
                      </Box>
                    );
                  })}

                  <Button
                    variant="outline"
                    size="sm"
                    mt={2}
                    onClick={() => setIsCreateOpen(true)}
                  >
                    <GoPlus />
                    Create new batch
                  </Button>
                </Flex>
              </Dialog.Body>
              <Dialog.Footer>
                <Dialog.ActionTrigger asChild>
                  <Button variant="outline" onClick={onClose}>
                    Cancel
                  </Button>
                </Dialog.ActionTrigger>
                <Button
                  colorPalette="brand"
                  loading={isPending}
                  disabled={!selectedBatchId}
                  onClick={handleAssign}
                >
                  Move here
                </Button>
              </Dialog.Footer>
              <Dialog.CloseTrigger asChild>
                <CloseButton size="sm" onClick={onClose} />
              </Dialog.CloseTrigger>
            </Dialog.Content>
          </Dialog.Positioner>
        </Portal>
      </Dialog.Root>

      <NewBatchModal
        open={isCreateOpen}
        onClose={() => setIsCreateOpen(false)}
        connectionId={connectionId}
        defaultName={`Batch ${batches.length + 1}`}
        tablesToAssign={tables}
        onCreated={() => {
          setIsCreateOpen(false);
          onClose();
        }}
      />
    </>
  );
};

export default BatchPickerModal;
