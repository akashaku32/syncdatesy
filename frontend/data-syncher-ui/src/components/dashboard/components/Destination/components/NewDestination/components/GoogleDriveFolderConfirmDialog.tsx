import { Button, CloseButton, Dialog, Portal, Text } from "@chakra-ui/react";

const GoogleDriveFolderConfirmDialog = ({
  open,
  folderName,
  isLoading,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  folderName: string;
  isLoading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) => {
  return (
    <Dialog.Root lazyMount open={open} role="alertdialog">
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content>
            <Dialog.Header>
              <Dialog.Title>Create Google Drive folder?</Dialog.Title>
            </Dialog.Header>
            <Dialog.Body>
              The folder{" "}
              <Text as="span" fontWeight="semibold">
                &ldquo;{folderName}&rdquo;
              </Text>{" "}
              does not exist in your Google Drive. Do you want to create it
              automatically?
            </Dialog.Body>
            <Dialog.Footer>
              <Dialog.ActionTrigger asChild>
                <Button variant="outline" onClick={onCancel}>
                  No, keep editing
                </Button>
              </Dialog.ActionTrigger>
              <Button
                colorPalette="brand"
                loading={isLoading}
                onClick={onConfirm}
              >
                Yes, create folder
              </Button>
            </Dialog.Footer>
            <Dialog.CloseTrigger asChild>
              <CloseButton size="sm" onClick={onCancel} />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default GoogleDriveFolderConfirmDialog;
