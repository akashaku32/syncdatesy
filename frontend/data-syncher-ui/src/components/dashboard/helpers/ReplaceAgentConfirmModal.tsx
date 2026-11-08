import React from "react";

import {
  Box,
  Button,
  CloseButton,
  Dialog,
  Flex,
  Portal,
  Text,
  VStack,
} from "@chakra-ui/react";

import { FiAlertTriangle, FiRefreshCw } from "react-icons/fi";

import { type ConnectedConnector } from "@/queryOptions/connector/useFetchCompanyProxyAgent";

interface ReplaceAgentConfirmModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  isGenerating?: boolean;
  connectedConnectors?: ConnectedConnector[];
  currentConnectorId?: string | number;
  currentConnectorName?: string;
}

const ReplaceAgentConfirmModal: React.FC<ReplaceAgentConfirmModalProps> = ({
  open,
  onClose,
  onConfirm,
  isGenerating = false,
  connectedConnectors = [],
  currentConnectorId,
  currentConnectorName,
}) => {
  // Exclude the current connector being created/edited from the affected list
  const otherConnectors = connectedConnectors.filter((c) => {
    if (currentConnectorId && String(c.id) === String(currentConnectorId)) {
      return false;
    }
    if (
      currentConnectorName &&
      c.name &&
      c.name.trim().toLowerCase() === currentConnectorName.trim().toLowerCase()
    ) {
      return false;
    }
    return true;
  });

  return (
    <Dialog.Root lazyMount open={open} role="alertdialog">
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content maxW="sm" borderRadius="md">
            <Dialog.Header pb={2}>
              <Flex align="center" gap={2}>
                <FiAlertTriangle size={20} color="#EAB308" />
                <Dialog.Title fontSize="lg" fontWeight="bold">
                  Regenerate Agent Config?
                </Dialog.Title>
              </Flex>
            </Dialog.Header>

            <Dialog.Body py={3}>
              <VStack align="stretch" gap={3}>
                <Text fontSize="sm" color="gray.700" lineHeight="relaxed">
                  Generating a new{" "}
                  <Text as="span" fontFamily="monospace" fontWeight="bold">
                    config.json
                  </Text>{" "}
                  will update your company's active proxy agent UUID. This is a{" "}
                  <Text as="span" fontWeight="semibold">
                    company-wide action
                  </Text>
                  .
                </Text>

                {otherConnectors.length > 0 && (
                  <Box
                    bg="gray.50"
                    borderWidth="1px"
                    borderColor="gray.200"
                    borderRadius="md"
                    p={3}
                  >
                    <Text
                      fontSize="xs"
                      fontWeight="semibold"
                      color="gray.800"
                      mb={1.5}
                    >
                      The following other connector(s) are currently using this
                      agent and will be affected:
                    </Text>
                    <VStack align="stretch" gap={1} pl={2}>
                      {otherConnectors.map((c) => (
                        <Text
                          key={c.id}
                          fontSize="xs"
                          color="gray.700"
                          fontWeight="medium"
                        >
                          &bull; {c.name}
                        </Text>
                      ))}
                    </VStack>
                  </Box>
                )}
              </VStack>
            </Dialog.Body>

            <Dialog.Footer pt={2}>
              <Dialog.ActionTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onClose}
                  disabled={isGenerating}
                >
                  Cancel
                </Button>
              </Dialog.ActionTrigger>

              <Button
                size="sm"
                colorPalette="brand"
                loading={isGenerating}
                disabled={isGenerating}
                onClick={onConfirm}
              >
                <FiRefreshCw style={{ marginRight: 6 }} />
                Regenerate
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

export default ReplaceAgentConfirmModal;
