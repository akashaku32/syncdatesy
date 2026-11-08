import { useReducer, useState } from "react";

import {
  Button,
  Field,
  Flex,
  NativeSelect,
  NumberInput,
  Stack,
} from "@chakra-ui/react";

import { CiTrash } from "react-icons/ci";
import { MdRefresh } from "react-icons/md";

import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import usePermissions from "@/hooks/usePermissions";
import useUpdateConnectionSettings from "@/queryOptions/connector/schema/useUpdateConnectionSettings";
import useTestConnection from "@/queryOptions/connector/useTestConnection";
import { type Connector } from "@/types/connectors";

import DeleteConfirmationDialog from "./DeleteConfirmationDialog";
import { safetyIntervalOptions } from "./helpers";
import { reducer } from "./reducer";

/** Matches backend DEFAULT_MIN_CHUNK_FLOOR in chunk_limit_resolver.py */
const PLAN_MIN_CHUNK_FLOOR = 100;

const Form = (props: Connector) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [chunkCountError, setChunkCountError] = useState<string | null>(null);
  const { can } = usePermissions();

  const canEdit = can("can_edit_connection_settings");
  const canDelete = can("can_delete_connectors");

  const {
    safety_interval,
    chunk_count,
    effective_max_chunk,
    min_count,
    max_count,
    has_batches_in_pipeline = false,
  } = props;

  const deleteBlockedByPipeline = has_batches_in_pipeline;

  const minChunkCount = Math.max(min_count ?? 0, PLAN_MIN_CHUNK_FLOOR);
  const maxChunkCount = max_count ?? effective_max_chunk ?? minChunkCount;
  const transferPacketSize = chunk_count ?? minChunkCount;

  const initialFormState = {
    safety_interval: safety_interval ?? "",
    chunk_count: transferPacketSize,
  };

  const [formState, dispatch] = useReducer(reducer, initialFormState);

  const { mutate: updateSettings, isPending: isUpdateOperationPending } =
    useUpdateConnectionSettings({
      connectorId: props.connection_id,
    });

  const { mutate: testConnection, isPending: isTestOperationPending } =
    useTestConnection({ connectorId: props.connection_id });

  return (
    <Flex direction="column" gap={4} mb={8}>
      <Stack gap="8" flexWrap="wrap" direction="row">
        <Field.Root maxW="sm" disabled={!canEdit}>
          <Field.Label>Safety Interval</Field.Label>
          <NativeSelect.Root disabled={!canEdit}>
            <NativeSelect.Field
              value={formState.safety_interval}
              onChange={(e) =>
                dispatch({
                  type: "SET_FIELD",
                  field: "safety_interval",
                  value: e.currentTarget.value,
                })
              }
            >
              {safetyIntervalOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
        </Field.Root>

        <Field.Root maxW="sm" disabled={!canEdit} invalid={!!chunkCountError}>
          <Field.Label>Transfer packet size</Field.Label>
          <NumberInput.Root
            disabled={!canEdit}
            min={minChunkCount}
            max={maxChunkCount}
            value={String(formState.chunk_count ?? transferPacketSize)}
            onValueChange={(e) => {
              const value = Number(e.value);
              if (isNaN(value)) return;

              dispatch({
                type: "SET_FIELD",
                field: "chunk_count",
                value: value,
              });
              setChunkCountError(null);
            }}
          >
            <NumberInput.Control />
            <NumberInput.Input />
          </NumberInput.Root>
          {chunkCountError ? (
            <Field.ErrorText>{chunkCountError}</Field.ErrorText>
          ) : (
            <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
              Min count: {minChunkCount.toLocaleString()} | Max count:{" "}
              {maxChunkCount.toLocaleString()}
            </Field.HelperText>
          )}
        </Field.Root>
      </Stack>

      <Flex justifyContent={"space-between"} mt={4}>
        <Flex gap={4}>
          {canDelete && (
            <>
              {deleteBlockedByPipeline ? (
                <Tooltip content="Remove this connection's batches from Scheduling before deleting.">
                  <Button
                    variant="outline"
                    colorPalette="red"
                    color="red.500"
                    onClick={() => setShowDeleteDialog(true)}
                    disabled
                  >
                    <CiTrash />
                    Delete
                  </Button>
                </Tooltip>
              ) : (
                <Button
                  variant="outline"
                  colorPalette="red"
                  color="red.500"
                  onClick={() => setShowDeleteDialog(true)}
                >
                  <CiTrash />
                  Delete
                </Button>
              )}
            </>
          )}
        </Flex>
        <Flex gap={4}>
          <Button
            variant="outline"
            colorPalette="brand"
            color="brand.500"
            loading={isTestOperationPending}
            onClick={() =>
              testConnection(undefined, {
                onSuccess: (response) => {
                  toaster.success({
                    title:
                      response.data?.message || "Connection test initiated",
                  });
                },
              })
            }
          >
            <MdRefresh />
            Test connection
          </Button>
          <Button
            colorPalette="brand"
            disabled={!canEdit}
            onClick={() => {
              setChunkCountError(null);
              updateSettings(
                {
                  safety_interval: formState.safety_interval,
                  chunk_count: formState.chunk_count,
                },
                {
                  onSuccess: () => {
                    toaster.success({
                      title: "Connector settings updated",
                    });
                  },
                  onError: (error: unknown) => {
                    const err = error as {
                      response?: {
                        data?: {
                          chunk_count?: string | string[];
                          message?: string;
                        };
                      };
                    };
                    const errors = err.response?.data;
                    if (errors && (errors.chunk_count || errors.message)) {
                      const msg =
                        (Array.isArray(errors.chunk_count)
                          ? errors.chunk_count[0]
                          : errors.chunk_count || errors.message) || null;
                      setChunkCountError(msg);
                    }
                  },
                },
              );
            }}
            loading={isUpdateOperationPending}
          >
            <MdRefresh />
            Update
          </Button>
        </Flex>
      </Flex>

      {showDeleteDialog && (
        <DeleteConfirmationDialog
          open={showDeleteDialog}
          setShowDeleteDialog={setShowDeleteDialog}
          connectorId={props.connection_id}
        />
      )}
    </Flex>
  );
};

export default Form;
