import { Box, Flex, Image, Text } from "@chakra-ui/react";

import { LOGO_STRIP_CONNECTORS } from "@/constants/marketing-connectors";

export default function ConnectorLogoStrip() {
  return (
    <Box
      w="full"
      py={{ base: 8, md: 10 }}
      bg="bg.canvas"
      borderYWidth="1px"
      borderColor="border.subtle"
    >
      <Text
        textAlign="center"
        fontSize="xs"
        fontWeight="bold"
        letterSpacing="0.14em"
        textTransform="uppercase"
        color="fg.muted"
        mb={2}
      >
        Sources &amp; destinations we support
      </Text>
      <Text textAlign="center" fontSize="sm" color="fg.muted" mb={6} px={4}>
        CRMs and ERPs on one side; Snowflake, Databricks, cloud storage,
        SharePoint, SFTP, Google Drive, and more on the other.
      </Text>
      <Flex
        gap={{ base: 5, md: 8 }}
        justify="center"
        align="center"
        flexWrap="wrap"
        px={4}
        maxW="container.xl"
        mx="auto"
      >
        {LOGO_STRIP_CONNECTORS.map((logo) => (
          <Image
            key={logo.label}
            src={logo.src}
            alt={logo.label}
            title={logo.label}
            h={{ base: "26px", md: "32px" }}
            w="auto"
            maxW="96px"
            objectFit="contain"
            opacity={0.88}
            filter="grayscale(15%)"
            transition="opacity 0.2s, filter 0.2s"
            _hover={{ opacity: 1, filter: "grayscale(0%)" }}
          />
        ))}
      </Flex>
    </Box>
  );
}
