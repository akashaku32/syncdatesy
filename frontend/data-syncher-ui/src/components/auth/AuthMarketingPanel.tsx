import { Box, Heading, List, Stack, Text } from "@chakra-ui/react";

import { FiLock, FiRefreshCw, FiShuffle } from "react-icons/fi";

import LogoWhite from "@/assets/images/logo-white.svg";
import { APP_NAME, APP_TAGLINE } from "@/constants/branding";

const HIGHLIGHTS = [
  {
    icon: FiShuffle,
    title: "Connect anything",
    body: "Salesforce, SAP, Snowflake, Databricks, files, and more—in one control plane.",
  },
  {
    icon: FiRefreshCw,
    title: "Schedule with confidence",
    body: "Visual pipelines, batch runs, and live status so teams know what synced and when.",
  },
  {
    icon: FiLock,
    title: "Enterprise-ready",
    body: "Tenant isolation, proxy agents for on-prem sources, and secrets kept out of git.",
  },
];

export default function AuthMarketingPanel() {
  return (
    <Box
      position="relative"
      overflow="hidden"
      h="full"
      minH={{ md: "100%" }}
      color="white"
      px={{ base: 8, lg: 12 }}
      py={{ base: 10, lg: 14 }}
      display="flex"
      flexDirection="column"
      justifyContent="center"
      bg="brand.800"
      backgroundImage="linear-gradient(155deg, #0f766e 0%, #0369a1 48%, #14b8a6 100%)"
    >
      <Box
        position="absolute"
        inset={0}
        backgroundImage={`url("${LogoWhite}")`}
        backgroundRepeat="no-repeat"
        backgroundPosition="right -40px bottom -20px"
        backgroundSize="420px"
        pointerEvents="none"
      />
      <Stack gap={8} position="relative" zIndex={1} maxW="lg">
        <Stack gap={3}>
          <Text
            fontSize="sm"
            fontWeight="semibold"
            letterSpacing="wider"
            textTransform="uppercase"
            opacity={0.9}
          >
            Data integration platform
          </Text>
          <Heading size="3xl" lineHeight="shorter">
            {APP_NAME}
          </Heading>
          <Text fontSize="lg" opacity={0.95} lineHeight="tall">
            {APP_TAGLINE}
          </Text>
        </Stack>
        <List.Root gap={4} variant="plain">
          {HIGHLIGHTS.map(({ icon: Icon, title, body }) => (
            <List.Item
              key={title}
              display="flex"
              gap={3}
              alignItems="flex-start"
            >
              <Box
                mt={1}
                p={2}
                borderRadius="lg"
                bg="whiteAlpha.200"
                lineHeight={0}
              >
                <Icon size={20} />
              </Box>
              <Box>
                <Text fontWeight="semibold">{title}</Text>
                <Text fontSize="sm" opacity={0.9}>
                  {body}
                </Text>
              </Box>
            </List.Item>
          ))}
        </List.Root>
      </Stack>
    </Box>
  );
}
