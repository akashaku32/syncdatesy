import React, { useState } from "react";

import { Box, Button, Field, Flex, Text } from "@chakra-ui/react";

import { FiDownload, FiRefreshCw, FiShield } from "react-icons/fi";

import { toaster } from "@/components/ui/toaster";
import ClientRoutes from "@/constants/client-routes";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import useFetchCompanyProxyAgent from "@/queryOptions/connector/useFetchCompanyProxyAgent";
import { type FieldConfig } from "@/types/form";

import ReplaceAgentConfirmModal from "./ReplaceAgentConfirmModal";

interface ProxyAgentGeneratorProps {
  field?: FieldConfig;
  value?: string;
  connectorId?: string | number;
  connectorName?: string;
  isEditMode?: boolean;
  agentName?: string;
  agentDownloadUrl?: string;
  onChange: (_agentUuid: string) => void;
  disabled?: boolean;
  error?: string;
  onValidate?: () => boolean;
}

const defaultDownloadRoute = `${ClientRoutes.DASHBOARD}/${ClientRoutes.ACCOUNT_SETTINGS.ROOT}/${ClientRoutes.ACCOUNT_SETTINGS.AGENT_DOWNLOADS}`;

const ProxyAgentGenerator: React.FC<ProxyAgentGeneratorProps> = ({
  field,
  value = "",
  connectorId,
  connectorName,
  isEditMode = false,
  agentName = "Datasyncher Agent",
  agentDownloadUrl = defaultDownloadRoute,
  onChange,
  disabled = false,
  error,
  onValidate,
}) => {
  const [hasDownloadedAgent, setHasDownloadedAgent] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isConfirmModalOpen, setIsConfirmModalOpen] = useState(false);

  const { data: companyAgentData } = useFetchCompanyProxyAgent();
  const hasCompanyAgent = !!companyAgentData?.has_agent;
  const companyAgentUuid = companyAgentData?.agent_uuid || "";
  const connectedConnectors = companyAgentData?.connected_connectors || [];

  const handleGenerateAndDownloadConfig = async () => {
    if (disabled || isGenerating) return;

    if (onValidate && !onValidate()) {
      toaster.error({
        title: "Required fields missing",
        description:
          "Please fill in all required fields before generating agent config.",
      });
      return;
    }

    setIsGenerating(true);
    try {
      const response = await AxiosInstance.post<Blob>(
        ServerRoutes.proxyAgent.generate(),
        {
          agent_name: agentName || "Datasyncher Agent",
        },
        {
          responseType: "blob",
        },
      );

      // Extract blob text & parse agent_uuid
      const blobText = await response.data.text();
      let agentUuid = "";
      try {
        const parsed = JSON.parse(blobText);
        agentUuid =
          parsed.agent_uuid ||
          parsed.agent_id ||
          parsed.uuid ||
          parsed.id ||
          "";
      } catch {
        // Blob might be formatted differently or binary json
      }

      // Extract filename from response headers if available
      const contentDisposition =
        (response.headers?.["content-disposition"] as string | undefined) ||
        (response.headers?.["Content-Disposition"] as string | undefined);
      let filename = "config.json";
      if (contentDisposition) {
        const match = contentDisposition.match(/filename="?([^";]+)"?/);
        if (match && match[1]) {
          filename = match[1];
        }
      }

      // If agent_uuid couldn't be parsed directly from JSON body, attempt extracting from filename
      if (!agentUuid && filename) {
        const uuidMatch = filename.match(
          /config-agent-(.+)\.json|config-(.+)\.json/,
        );
        if (uuidMatch) {
          agentUuid = uuidMatch[1] || uuidMatch[2] || "";
        }
      }

      // Trigger file download in browser
      const blob = new Blob([response.data], { type: "application/json" });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      // Update form field with the extracted agent_uuid
      const finalUuid = agentUuid || `agent-${Date.now()}`;
      onChange(finalUuid);

      toaster.success({
        title: "Agent config generated successfully",
        description: `Downloaded ${filename} and linked proxy agent to connector.`,
      });
    } catch (err: unknown) {
      console.error("Error generating proxy agent config:", err);

      let errorMessage = "Failed to generate agent config. Please try again.";

      try {
        const blobObj =
          err instanceof Blob
            ? err
            : (err as { response?: { data?: unknown } })?.response?.data;

        if (blobObj instanceof Blob) {
          const raw = await blobObj.text();
          console.error("Backend error raw text:", raw);
          const parsed = JSON.parse(raw) as Record<string, unknown>;
          const msg =
            (parsed?.message as string) ||
            (parsed?.error as string) ||
            (parsed?.detail as string);
          if (msg) errorMessage = msg;
        } else if (err && typeof err === "object") {
          const errObj = err as Record<string, unknown>;
          const msg =
            (errObj?.message as string) ||
            (errObj?.error as string) ||
            (errObj?.detail as string);
          if (msg) errorMessage = msg;
        }
      } catch (parseErr) {
        console.error("Failed to parse error response blob:", parseErr);
      }

      toaster.error({
        title: "Generation failed",
        description: errorMessage,
      });
    } finally {
      setIsGenerating(false);
    }
  };

  // Check if current connector is in connected_connectors list from status API
  const isConnectorInStatusList =
    !!connectorId &&
    connectedConnectors.some((c) => String(c.id) === String(connectorId));

  // State C: Connector is linked (Agent configured)
  // Valid ONLY when the company has an active agent AND (form value set / connector in status list / edit mode)
  const isStateC =
    hasCompanyAgent && (!!value || isConnectorInStatusList || isEditMode);

  // State B: Company has an existing agent, but this connector is unlinked (New connection creation)
  const isStateB = hasCompanyAgent && !isStateC;

  return (
    <Field.Root invalid={!!error} w="full">
      <Field.Label fontSize="sm" fontWeight="semibold" mb={2}>
        {field?.label || "Proxy Agent"}
        {field?.required && (
          <Text as="span" color="red.500" ml={1}>
            *
          </Text>
        )}
      </Field.Label>

      {/* STATE C: Linked / Edit Mode */}
      {isStateC && (
        <Flex
          align="center"
          justify="space-between"
          gap={3}
          bg="white"
          border="1px solid"
          borderColor="purple.200"
          borderRadius="xl"
          px={5}
          py={4}
          w="full"
          boxShadow="xs"
        >
          <Flex align="center" gap={3}>
            <Box w={2.5} h={2.5} borderRadius="full" bg="brand.500" />
            <Text fontSize="sm" color="gray.900" fontWeight="bold">
              Agent configured
            </Text>
          </Flex>

          <Button
            size="sm"
            variant="outline"
            borderColor="gray.300"
            color="gray.800"
            bg="white"
            _hover={{ bg: "gray.50" }}
            onClick={() => setIsConfirmModalOpen(true)}
            loading={isGenerating}
            disabled={disabled || isGenerating}
            borderRadius="lg"
            px={3.5}
          >
            <FiDownload style={{ marginRight: 6 }} />
            Download config
          </Button>
        </Flex>
      )}

      {/* STATE B: Company has an agent, unlinked */}
      {isStateB && (
        <Box
          w="full"
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="xl"
          p={5}
          bg="white"
          boxShadow="xs"
        >
          <Flex gap={3.5} align="flex-start">
            <Flex
              w={8}
              h={8}
              borderRadius="lg"
              bg="purple.50"
              color="brand.600"
              align="center"
              justify="center"
              flexShrink={0}
              mt={0.5}
            >
              <FiShield size={20} />
            </Flex>

            <Flex direction="column" gap={1.5} flex={1}>
              <Text
                fontSize="sm"
                fontWeight="bold"
                color="gray.900"
                lineHeight="tight"
              >
                Your company already has an agent
              </Text>
              <Text fontSize="xs" color="gray.500" lineHeight="normal">
                <Text
                  as="span"
                  fontFamily="monospace"
                  fontWeight="bold"
                  bg="gray.100"
                  color="gray.800"
                  px={1.5}
                  py={0.5}
                  borderRadius="sm"
                >
                  {companyAgentUuid}
                </Text>{" "}
                is active and can be reused for this connector.
              </Text>

              <Flex align="center" gap={3} mt={3.5}>
                <Button
                  size="sm"
                  colorPalette="brand"
                  onClick={() => onChange(companyAgentUuid)}
                  disabled={disabled}
                  borderRadius="md"
                  px={4}
                >
                  Use this agent
                </Button>

                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  display="inline-flex"
                  alignItems="center"
                  gap={1}
                  fontSize="xs"
                  color="gray.600"
                  fontWeight="medium"
                  _hover={{ color: "brand.600" }}
                  onClick={() => setIsConfirmModalOpen(true)}
                  disabled={disabled || isGenerating}
                  cursor="pointer"
                  h="auto"
                  p={0}
                >
                  <FiRefreshCw size={13} />
                  Generate new instead
                </Button>
              </Flex>
            </Flex>
          </Flex>
        </Box>
      )}

      {/* STATE A: No agent yet (Full Wizard) */}
      {!isStateC && !isStateB && (
        <Box
          w="full"
          borderWidth="1px"
          borderColor="gray.200"
          borderRadius="xl"
          p={5}
          bg="white"
          boxShadow="xs"
        >
          {/* Step 1 */}
          <Flex gap={3.5} align="flex-start">
            <Flex
              w={6}
              h={6}
              borderRadius="full"
              borderWidth="1.5px"
              borderColor={hasDownloadedAgent ? "brand.500" : "gray.400"}
              color={hasDownloadedAgent ? "brand.600" : "gray.500"}
              align="center"
              justify="center"
              fontSize="xs"
              fontWeight="bold"
              flexShrink={0}
              mt={0.5}
            >
              1
            </Flex>

            <Flex direction="column" gap={1.5} flex={1}>
              <Text
                fontSize="sm"
                fontWeight="bold"
                color="gray.900"
                lineHeight="tight"
              >
                Make sure you have downloaded the agent
              </Text>
              <Text fontSize="xs" color="gray.500" lineHeight="normal">
                The{" "}
                <Box
                  asChild
                  color="brand.600"
                  fontWeight="semibold"
                  textDecoration="underline"
                  _hover={{ color: "brand.700" }}
                >
                  <a
                    href={agentDownloadUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    agent download
                  </a>
                </Box>{" "}
                contains the proxy agent. Make sure you've downloaded this
                before proceeding.
              </Text>

              <Flex
                align="center"
                gap={2.5}
                mt={2}
                cursor={disabled ? "not-allowed" : "pointer"}
                onClick={() => {
                  if (!disabled) setHasDownloadedAgent((prev) => !prev);
                }}
                width="fit-content"
              >
                <Box
                  w={4}
                  h={4}
                  borderRadius="full"
                  borderWidth="1.5px"
                  borderColor={hasDownloadedAgent ? "brand.500" : "gray.300"}
                  display="flex"
                  alignItems="center"
                  justifyContent="center"
                  bg="white"
                  transition="border-color 0.2s"
                >
                  {hasDownloadedAgent && (
                    <Box w={2} h={2} borderRadius="full" bg="brand.500" />
                  )}
                </Box>
                <Text
                  fontSize="xs"
                  fontWeight="semibold"
                  color="gray.800"
                  userSelect="none"
                >
                  I have downloaded the agent.
                </Text>
              </Flex>
            </Flex>
          </Flex>

          {/* Divider */}
          <Box w="full" h="1px" bg="gray.100" my={4.5} />

          {/* Step 2 */}
          <Flex gap={3.5} align="flex-start">
            <Flex
              w={6}
              h={6}
              borderRadius="full"
              borderWidth="1.5px"
              borderColor={hasDownloadedAgent ? "brand.500" : "gray.300"}
              color={hasDownloadedAgent ? "brand.600" : "gray.400"}
              align="center"
              justify="center"
              fontSize="xs"
              fontWeight="bold"
              flexShrink={0}
              mt={0.5}
            >
              2
            </Flex>

            <Flex direction="column" gap={1.5} flex={1}>
              <Text
                fontSize="sm"
                fontWeight="bold"
                color="gray.900"
                lineHeight="tight"
              >
                Generate agent config
              </Text>
              <Text fontSize="xs" color="gray.500" lineHeight="normal">
                We'll create a config.json and link this agent to this connector
                automatically.
              </Text>

              <Box mt={2.5}>
                <Button
                  size="sm"
                  colorPalette="brand"
                  disabled={!hasDownloadedAgent || disabled || isGenerating}
                  loading={isGenerating}
                  onClick={handleGenerateAndDownloadConfig}
                  px={4}
                  borderRadius="md"
                >
                  <FiDownload style={{ marginRight: 6 }} />
                  Generate & download config
                </Button>
              </Box>
            </Flex>
          </Flex>
        </Box>
      )}

      {field?.description && (
        <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
          {field.description}
        </Field.HelperText>
      )}

      {error && (
        <Field.ErrorText fontSize="xs" mt={1}>
          {error}
        </Field.ErrorText>
      )}

      {/* Confirmation Modal */}
      <ReplaceAgentConfirmModal
        open={isConfirmModalOpen}
        onClose={() => setIsConfirmModalOpen(false)}
        onConfirm={async () => {
          setIsConfirmModalOpen(false);
          await handleGenerateAndDownloadConfig();
        }}
        isGenerating={isGenerating}
        connectedConnectors={connectedConnectors}
        currentConnectorId={connectorId}
        currentConnectorName={connectorName}
      />
    </Field.Root>
  );
};

export default ProxyAgentGenerator;
