import { Button, CloseButton, Dialog, Portal, Text } from "@chakra-ui/react";

import { useNavigate } from "react-router";

import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import useDeleteConnection from "@/queryOptions/connector/useDeleteConnection";

const DeleteConfirmationDialog = ({
  open,
  setShowDeleteDialog,
  connectorId,
}: {
  open: boolean;
  setShowDeleteDialog: (_open: boolean) => void;
  connectorId: number;
}) => {
  const navigate = useNavigate();
  const { mutate: deleteConnection, isPending: isDeleteOperationPending } =
    useDeleteConnection({ connectorId });

  return (
    <Dialog.Root lazyMount open={open} role="alertdialog">
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content>
            <Dialog.Header>
              <Dialog.Title>Delete confirmation</Dialog.Title>
            </Dialog.Header>
            <Dialog.Body>
              Are you sure you want to delete this connection?
              <Text fontWeight="semibold">
                Note: This action is irreversible
              </Text>
            </Dialog.Body>
            <Dialog.Footer>
              <Dialog.ActionTrigger asChild>
                <Button
                  variant="outline"
                  onClick={() => setShowDeleteDialog(false)}
                >
                  Cancel
                </Button>
              </Dialog.ActionTrigger>
              <Button
                colorPalette="red"
                loading={isDeleteOperationPending}
                onClick={() =>
                  deleteConnection(undefined, {
                    onSuccess: () => {
                      toaster.success({
                        title: "Connection deleted successfully",
                        description: `The connection has been deleted.`,
                      });
                      setShowDeleteDialog(false);
                      navigate(
                        `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
                      );
                    },
                    onError: (err: unknown) => {
                      const data = (
                        err as {
                          response?: {
                            data?: {
                              message?: string;
                              detail?: string;
                            };
                          };
                        }
                      )?.response?.data;
                      const message =
                        data?.message ?? "Could not delete connection";
                      toaster.error({
                        title: message,
                        description: data?.detail,
                      });
                    },
                  })
                }
              >
                Delete
              </Button>
            </Dialog.Footer>
            <Dialog.CloseTrigger asChild>
              <CloseButton size="sm" />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default DeleteConfirmationDialog;
