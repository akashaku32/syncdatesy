import React from "react";

import { Badge, Box, Button, Flex, Text, VStack } from "@chakra-ui/react";

import { FiDownload, FiExternalLink, FiTerminal } from "react-icons/fi";
import { LuAppWindow } from "react-icons/lu";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import { toaster } from "@/components/ui/toaster";
import { VIEW_CONFIG } from "@/constants/view-config";

interface AgentPackage {
  id: string;
  os: string;
  version: string;
  filename: string;
  size: string;
  downloadUrl: string;
  icon: React.ReactNode;
}

const agentPackages: AgentPackage[] = [
  {
    id: "windows",
    os: "Windows",
    version: "v6.2.0",
    filename: "datasyncher-agent-6.2.0-setup.exe",
    size: "42 MB",
    downloadUrl:
      "https://drive.google.com/drive/folders/1oz6GQLj2w72ivfdyJVzRN-BtVD2XaxrR?usp=drive_link",
    icon: <LuAppWindow size={24} />,
  },
  {
    id: "linux",
    os: "Linux",
    version: "v6.2.0",
    filename: "datasyncher-agent-6.2.0-linux-setup.exe",
    size: "38 MB",
    downloadUrl:
      "https://drive.google.com/drive/folders/16G_XqsY0uMi4ggMVl6omGqH4ibtSSkLX?usp=drive_link",
    icon: <FiTerminal size={24} />,
  },
];

const AgentDownloads: React.FC = () => {
  const handleDownload = (pkg: AgentPackage) => {
    toaster.info({
      title: `Downloading ${pkg.os} Agent`,
      description: `Opening download link for ${pkg.filename}`,
    });
    window.open(pkg.downloadUrl, "_blank", "noopener,noreferrer");
  };

  return (
    <Flex direction="column" gap={VIEW_CONFIG.pageGap} w="full">
      <PageHeader
        breadcrumbs={[
          { label: "Account Settings" },
          { label: "Agent Downloads" },
        ]}
        title="Agent downloads"
        subtitle="Download the Datasyncher agent to route connections through your own network."
      />

      <VStack gap={4} align="stretch" maxW="2xl" mt={2}>
        {agentPackages.map((pkg) => (
          <Box
            key={pkg.id}
            borderWidth="1px"
            borderColor="gray.200"
            borderRadius="xl"
            p={5}
            bg="white"
            boxShadow="xs"
            transition="all 0.2s"
            _hover={{ borderColor: "brand.300", boxShadow: "sm" }}
          >
            <Flex align="center" gap={4}>
              {/* Icon Container */}
              <Flex
                w={12}
                h={12}
                borderRadius="lg"
                bg="purple.50"
                color="purple.600"
                align="center"
                justify="center"
                flexShrink={0}
              >
                {pkg.icon}
              </Flex>

              {/* Package Details */}
              <Flex direction="column" gap={2} flex={1}>
                <Flex align="center" gap={2}>
                  <Text fontSize="md" fontWeight="bold" color="gray.900">
                    {pkg.os}
                  </Text>
                  <Badge
                    colorPalette="purple"
                    variant="subtle"
                    fontSize="2xs"
                    px={2}
                    py={0.5}
                    borderRadius="full"
                  >
                    {pkg.version}
                  </Badge>
                </Flex>

                <Text fontSize="xs" color="gray.500">
                  {pkg.filename} &bull; {pkg.size}
                </Text>

                <Flex align="center" gap={3} mt={1}>
                  <Button
                    size="sm"
                    colorPalette="brand"
                    onClick={() => handleDownload(pkg)}
                    borderRadius="md"
                    px={4}
                  >
                    <FiDownload style={{ marginRight: 6 }} />
                    Download
                  </Button>

                  <Flex
                    asChild
                    align="center"
                    gap={1}
                    fontSize="xs"
                    color="gray.600"
                    fontWeight="medium"
                    _hover={{ color: "brand.600" }}
                  >
                    <a
                      href={pkg.downloadUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Release notes <FiExternalLink size={12} />
                    </a>
                  </Flex>
                </Flex>
              </Flex>
            </Flex>
          </Box>
        ))}
      </VStack>
    </Flex>
  );
};

export default AgentDownloads;
