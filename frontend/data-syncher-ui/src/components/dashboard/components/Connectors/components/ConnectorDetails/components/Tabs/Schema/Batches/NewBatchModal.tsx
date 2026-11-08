import { useState } from "react";

import {
  Button,
  CloseButton,
  Dialog,
  Field,
  Flex,
  Input,
  Portal,
} from "@chakra-ui/react";

import { AxiosError } from "axios";

import { toaster } from "@/components/ui/toaster";
import {
  useAddTablesToBatch,
  useCreateBatch,
} from "@/queryOptions/connector/schema/useBatches";
import { type MigrationBatch } from "@/types/connectors";

interface NewBatchModalProps {
  open: boolean;
  onClose: () => void;
  connectionId: number;
  /**
   * Suggested default name. Pass e.g. `Batch ${batches.length + 1}`.
   */
  defaultName?: string;
  /**
   * If provided, after creating the batch these tables will be assigned to it.
   */
  tablesToAssign?: string[];
  onCreated?: (_batch: MigrationBatch) => void;
}

const NewBatchModal = ({
  open,
  onClose,
  connectionId,
  defaultName,
  tablesToAssign,
  onCreated,
}: NewBatchModalProps) => {
  const [name, setName] = useState<string>(defaultName ?? "");

  const { mutate: createBatch, isPending: isCreating } =
    useCreateBatch(connectionId);
  const { mutateAsync: addTables, isPending: isAssigning } =
    useAddTablesToBatch(connectionId);

  const handleSubmit = () => {
    createBatch(
      {
        name: name.trim() || undefined,
        // Scheduling is configured from the dedicated Scheduling section.
        time_frequency: "15",
        execution_order: "parallel",
        schedule_type: "interval",
        schedule_config: {},
        sync_start_date: null,
      },
      {
        onSuccess: async (batch: MigrationBatch | undefined) => {
          if (!batch) {
            toaster.error({
              title: "Could not create batch",
              description: "Empty response.",
            });
            return;
          }
          toaster.success({ title: "Batch created" });

          if (tablesToAssign && tablesToAssign.length > 0) {
            try {
              await addTables({
                batchId: batch.id,
                payload: { tables: tablesToAssign },
              });
            } catch {
              toaster.warning({
                title: "Batch created, but could not assign tables",
              });
            }
          }

          onCreated?.(batch);
          onClose();
        },
        onError: (err: unknown) => {
          let description: string | undefined;
          if (err instanceof AxiosError) {
            const data = err.response?.data as
              | { error?: string; message?: string; detail?: string }
              | undefined;
            description =
              data?.error ?? data?.message ?? data?.detail ?? err.message;
          } else if (err instanceof Error) {
            description = err.message;
          }
          toaster.error({
            title: "Could not create batch",
            description,
          });
        },
      },
    );
  };

  return (
    <Dialog.Root
      lazyMount
      open={open}
      size="md"
      onOpenChange={(e) => {
        if (e.open) {
          setName(defaultName ?? "");
        }
      }}
    >
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content>
            <Dialog.Header>
              <Dialog.Title>Create new batch</Dialog.Title>
            </Dialog.Header>
            <Dialog.Body>
              <Flex direction="column" gap={4}>
                <Field.Root>
                  <Field.Label>Name</Field.Label>
                  <Input
                    size="sm"
                    placeholder={defaultName ?? "Batch name"}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    autoFocus
                  />
                </Field.Root>
                <Field.Root>
                  <Field.HelperText>
                    Batch schedule and execution are managed in Dashboard
                    Scheduling.
                  </Field.HelperText>
                </Field.Root>
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
                loading={isCreating || isAssigning}
                onClick={handleSubmit}
              >
                Create batch
              </Button>
            </Dialog.Footer>
            <Dialog.CloseTrigger asChild>
              <CloseButton size="sm" onClick={onClose} />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default NewBatchModal;
