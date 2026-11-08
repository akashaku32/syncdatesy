import { Box, Flex, Heading, List, Stack, Text } from "@chakra-ui/react";

import { FiLock, FiRefreshCw, FiShuffle } from "react-icons/fi";

import LogoWhite from "@/assets/images/logo-white.svg";
import { AppBrand } from "@/components/branding/AppBrand";
import { APP_TAGLINE } from "@/constants/branding";
import { HERO_GRADIENT } from "@/constants/marketing-visual";

const HIGHLIGHTS = [
  {
    icon: FiShuffle,
    title: "Connect anything",
    body: "Salesforce, SAP, Postgres, and more—into Snowflake, Databricks, S3, SFTP, SharePoint, and other targets.",
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
      minH="100dvh"
      color="white"
      px={{ base: 8, lg: 12 }}
      py={{ base: 10, lg: 12 }}
      display="flex"
      flexDirection="column"
      justifyContent="space-between"
      bg="navy.950"
      backgroundImage={HERO_GRADIENT}
    >
      <Box
        aria-hidden
        position="absolute"
        top="-20%"
        right="-15%"
        w="420px"
        h="420px"
        borderRadius="full"
        bg="rgba(45, 212, 191, 0.22)"
        filter="blur(80px)"
        pointerEvents="none"
      />
      <Box
        aria-hidden
        position="absolute"
        bottom="-10%"
        left="-20%"
        w="360px"
        h="360px"
        borderRadius="full"
        bg="rgba(14, 165, 233, 0.12)"
        filter="blur(72px)"
        pointerEvents="none"
      />
      <Box
        position="absolute"
        inset={0}
        backgroundImage={`url("${LogoWhite}")`}
        backgroundRepeat="no-repeat"
        backgroundPosition="right -24px bottom -32px"
        backgroundSize="min(380px, 55vw)"
        opacity={0.35}
        pointerEvents="none"
      />

      <Flex position="relative" zIndex={1} pt={2}>
        <AppBrand size="md" nameColor="white" />
      </Flex>

      <Stack
        gap={10}
        position="relative"
        zIndex={1}
        maxW="md"
        flex="1"
        justify="center"
        my={8}
      >
        <Stack gap={4}>
          <Text
            fontSize="xs"
            fontWeight="bold"
            letterSpacing="0.14em"
            textTransform="uppercase"
            color="brand.200"
          >
            Data integration platform
          </Text>
          <Heading
            fontSize={{ lg: "4xl" }}
            lineHeight="1.1"
            letterSpacing="-0.03em"
            fontWeight="bold"
          >
            Sync once.
            <br />
            Trust always.
          </Heading>
          <Text
            fontSize="lg"
            color="whiteAlpha.900"
            lineHeight="tall"
            maxW="sm"
          >
            {APP_TAGLINE}
          </Text>
        </Stack>
        <List.Root gap={5} variant="plain">
          {HIGHLIGHTS.map(({ icon: Icon, title, body }) => (
            <List.Item
              key={title}
              display="flex"
              gap={4}
              alignItems="flex-start"
            >
              <Flex
                mt={0.5}
                p={2.5}
                borderRadius="xl"
                bg="whiteAlpha.150"
                borderWidth="1px"
                borderColor="whiteAlpha.250"
                lineHeight={0}
                flexShrink={0}
              >
                <Icon size={18} />
              </Flex>
              <Box>
                <Text fontWeight="semibold" fontSize="md">
                  {title}
                </Text>
                <Text
                  fontSize="sm"
                  color="whiteAlpha.800"
                  lineHeight="tall"
                  mt={0.5}
                >
                  {body}
                </Text>
              </Box>
            </List.Item>
          ))}
        </List.Root>
      </Stack>

      <Text
        position="relative"
        zIndex={1}
        fontSize="xs"
        color="whiteAlpha.700"
        pb={2}
      >
        SOC2-minded architecture · Tenant-scoped by default
      </Text>
    </Box>
  );
}
