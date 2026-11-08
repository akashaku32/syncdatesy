import { CloseButton, Dialog, Portal } from "@chakra-ui/react";

import { type InvoiceItem } from "@/types/billing";

import PaymentWrapper from "./PaymentWrapper";

interface PaymentDialogProps {
  open: boolean;
  invoice: InvoiceItem | null;
  onClose: () => void;
  onSuccess: () => void;
}

const PaymentDialog = ({
  open,
  invoice,
  onClose,
  onSuccess,
}: PaymentDialogProps) => {
  if (!open || !invoice?.id) return null;

  return (
    <Dialog.Root open={open} closeOnInteractOutside={false}>
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content maxW="520px" w="90vw">
            <Dialog.Header>
              <Dialog.Title>Pay Invoice</Dialog.Title>
              <Dialog.CloseTrigger asChild>
                <CloseButton
                  position="absolute"
                  top="10px"
                  right="10px"
                  onClick={onClose}
                />
              </Dialog.CloseTrigger>
            </Dialog.Header>
            <Dialog.Body>
              <PaymentWrapper
                invoiceId={invoice.id}
                invoiceNumber={invoice.invoice_number}
                amount={invoice.total_amount}
                onCancel={onClose}
                onSuccess={onSuccess}
              />
            </Dialog.Body>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default PaymentDialog;
