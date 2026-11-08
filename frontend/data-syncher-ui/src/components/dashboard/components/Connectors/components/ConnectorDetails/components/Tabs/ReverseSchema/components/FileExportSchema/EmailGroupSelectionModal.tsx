import { useEffect, useState } from "react";

import {
  Button,
  Checkbox,
  CloseButton,
  Dialog,
  Flex,
  Portal,
  Text,
  VStack,
} from "@chakra-ui/react";

import { type EmailGroup } from "@/types/emailGroups";

interface EmailGroupSelectionModalProps {
  open: boolean;
  onClose: () => void;
  tableName: string;
  emailGroups: EmailGroup[];
  initialSelectedGroupIds: number[];
  onSave: (_groupIds: number[]) => void;
  isSaving?: boolean;
}

const EmailGroupSelectionModal = ({
  open,
  onClose,
  tableName,
  emailGroups,
  initialSelectedGroupIds,
  onSave,
  isSaving,
}: EmailGroupSelectionModalProps) => {
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => {
        setSelectedIds(initialSelectedGroupIds || []);
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [open, initialSelectedGroupIds]);

  const toggleGroup = (id: number) => {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  const handleSave = () => {
    onSave(selectedIds);
    onClose();
  };

  return (
    <Dialog.Root lazyMount open={open} size="md">
      <Portal>
        <Dialog.Backdrop bg="blackAlpha.600" backdropFilter="blur(4px)" />
        <Dialog.Positioner>
          <Dialog.Content
            borderRadius="xl"
            boxShadow="2xl"
            bg="white"
            overflow="hidden"
          >
            <Dialog.Header
              bg="brand.50"
              borderBottomWidth="1px"
              borderColor="brand.100"
            >
              <Dialog.Title color="brand.800" fontWeight="bold">
                Email Notifications: {tableName}
              </Dialog.Title>
            </Dialog.Header>
            <Dialog.Body p={6}>
              <Text fontSize="sm" color="gray.600" mb={4}>
                Select the email groups to be notified upon successful file
                export for this table.
              </Text>

              {emailGroups.length === 0 ? (
                <Text
                  fontSize="sm"
                  color="gray.500"
                  fontStyle="italic"
                  py={4}
                  textAlign="center"
                >
                  No email groups configured. Go to Account Settings &gt; Email
                  to create one.
                </Text>
              ) : (
                <VStack align="stretch" gap={3} maxH="300px" overflowY="auto">
                  {emailGroups.map((group) => {
                    const isChecked = selectedIds.includes(group.id);
                    return (
                      <Flex
                        key={group.id}
                        p={3}
                        bg={isChecked ? "brand.50" : "gray.50"}
                        borderWidth={1}
                        borderColor={isChecked ? "brand.200" : "gray.200"}
                        borderRadius="lg"
                        alignItems="center"
                        gap={3}
                        cursor="pointer"
                        onClick={() => toggleGroup(group.id)}
                        _hover={{ bg: isChecked ? "brand.50" : "gray.100" }}
                        transition="all 0.2s"
                      >
                        <Checkbox.Root
                          colorPalette="brand"
                          checked={isChecked}
                          onCheckedChange={() => toggleGroup(group.id)}
                        >
                          <Checkbox.HiddenInput />
                          <Checkbox.Control />
                        </Checkbox.Root>
                        <Flex direction="column" flex={1} minW={0}>
                          <Text
                            fontSize="sm"
                            fontWeight="semibold"
                            color="gray.800"
                          >
                            {group.name}
                          </Text>
                          <Text
                            fontSize="xs"
                            color="gray.500"
                            whiteSpace="nowrap"
                            overflowX="auto"
                            css={{
                              "&::-webkit-scrollbar": { display: "none" },
                              msOverflowStyle: "none",
                              scrollbarWidth: "none",
                            }}
                          >
                            {group.email_addresses.join(", ")}
                          </Text>
                        </Flex>
                      </Flex>
                    );
                  })}
                </VStack>
              )}
            </Dialog.Body>
            <Dialog.Footer
              bg="gray.50"
              borderTopWidth="1px"
              borderColor="gray.100"
              p={4}
            >
              <Button
                variant="ghost"
                onClick={onClose}
                mr={3}
                disabled={isSaving}
              >
                Cancel
              </Button>
              <Button
                colorPalette="brand"
                onClick={handleSave}
                px={6}
                borderRadius="full"
                loading={isSaving}
              >
                Save Settings
              </Button>
            </Dialog.Footer>
            <Dialog.CloseTrigger asChild>
              <CloseButton
                size="sm"
                onClick={onClose}
                position="absolute"
                right={4}
                top={4}
              />
            </Dialog.CloseTrigger>
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
};

export default EmailGroupSelectionModal;
