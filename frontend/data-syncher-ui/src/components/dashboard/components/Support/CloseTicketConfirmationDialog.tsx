import { Button, CloseButton, Dialog, Portal } from "@chakra-ui/react";

import { toaster } from "@/components/ui/toaster";
import useCloseTicket from "@/queryOptions/support/useCloseTicket";

interface CloseTicketConfirmationDialogProps {
  open: boolean;
  onClose: () => void;
  ticketId: number | string;
}

const CloseTicketConfirmationDialog = ({
  open,
  onClose,
  ticketId,
}: CloseTicketConfirmationDialogProps) => {
  const { mutate: closeTicket, isPending: isCloseOperationPending } =
    useCloseTicket(ticketId);

  return (
    <Dialog.Root lazyMount open={open} role="alertdialog">
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content>
            <Dialog.Header>
              <Dialog.Title>Close confirmation</Dialog.Title>
            </Dialog.Header>
            <Dialog.Body>Do you want to close the ticket?</Dialog.Body>
            <Dialog.Footer>
              <Dialog.ActionTrigger asChild>
                <Button variant="outline" onClick={onClose}>
                  Cancel
                </Button>
              </Dialog.ActionTrigger>
              <Button
                colorPalette="brand"
                loading={isCloseOperationPending}
                onClick={() =>
                  closeTicket(undefined, {
                    onSuccess: () => {
                      toaster.success({
                        title: "Ticket closed successfully",
                        description: `The ticket has been closed.`,
                      });
                      onClose();
                    },
                  })
                }
              >
                Close
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

export default CloseTicketConfirmationDialog;
