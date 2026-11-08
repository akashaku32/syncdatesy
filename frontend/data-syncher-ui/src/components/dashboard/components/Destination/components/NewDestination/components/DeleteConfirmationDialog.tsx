import { Button, CloseButton, Dialog, Portal, Text } from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";
import useDeleteDestination from "@/queryOptions/destination/useDeleteDestination";

const DeleteConfirmationDialog = ({
  open,
  setShowDeleteDialog,
  destinationId,
  onSuccess,
}: {
  open: boolean;
  setShowDeleteDialog: (_open: boolean) => void;
  destinationId: number;
  onSuccess?: () => void;
}) => {
  const { mutate: deleteDestination, isPending: isDeleteOperationPending } =
    useDeleteDestination({ destinationId });

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
              Are you sure you want to delete this destination?
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
                  deleteDestination(undefined, {
                    onSuccess: () => {
                      toaster.success({
                        title: "Destination deleted successfully",
                        description: "The destination has been deleted.",
                      });
                      setShowDeleteDialog(false);
                      onSuccess?.();
                    },
                    onError: () => {
                      toaster.error({
                        title: "Failed to delete destination",
                        description:
                          "An error occurred while deleting the destination.",
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
