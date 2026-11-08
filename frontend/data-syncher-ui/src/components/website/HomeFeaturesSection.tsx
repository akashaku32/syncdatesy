import {
  Box,
  Container,
  Flex,
  Grid,
  Heading,
  List,
  SimpleGrid,
  Stack,
  Text,
} from "@chakra-ui/react";

import { FiCheck } from "react-icons/fi";

import {
  HOME_FEATURES,
  type HomeFeature,
  PLATFORM_PILLARS,
} from "@/constants/home-content";

const ACCENT_STYLES: Record<
  HomeFeature["accent"],
  { iconBg: string; iconColor: string; borderHover: string; topBar: string }
> = {
  brand: {
    iconBg: "brand.50",
    iconColor: "brand.600",
    borderHover: "brand.300",
    topBar: "brand.500",
  },
  accent: {
    iconBg: "accent.50",
    iconColor: "accent.600",
    borderHover: "accent.300",
    topBar: "accent.500",
  },
  navy: {
    iconBg: "navy.100",
    iconColor: "navy.800",
    borderHover: "brand.200",
    topBar: "navy.800",
  },
};

function FeatureCard({ feature }: { feature: HomeFeature }) {
  const styles = ACCENT_STYLES[feature.accent];
  const Icon = feature.icon;

  return (
    <Box
      position="relative"
      p={{ base: 6, md: 7 }}
      borderRadius="2xl"
      borderWidth="1px"
      borderColor="border.subtle"
      bg="bg.surface"
      overflow="hidden"
      h="full"
      transition="transform 0.2s, box-shadow 0.2s, border-color 0.2s"
      _hover={{
        borderColor: styles.borderHover,
        boxShadow: "0 20px 40px -20px rgba(13, 148, 136, 0.22)",
        transform: "translateY(-3px)",
      }}
    >
      <Box
        position="absolute"
        top={0}
        left={0}
        right={0}
        h="3px"
        bg={styles.topBar}
      />
      <Flex
        align="center"
        justify="center"
        w={14}
        h={14}
        borderRadius="xl"
        bg={styles.iconBg}
        color={styles.iconColor}
        mb={5}
      >
        <Icon size={28} />
      </Flex>
      <Heading size="lg" mb={2} letterSpacing="-0.02em">
        {feature.title}
      </Heading>
      <Text color="fg.muted" fontSize="sm" lineHeight="tall" mb={4}>
        {feature.description}
      </Text>
      <List.Root gap={2} variant="plain">
        {feature.bullets.map((bullet) => (
          <List.Item
            key={bullet}
            display="flex"
            alignItems="flex-start"
            gap={2}
            fontSize="sm"
            color="gray.700"
          >
            <Box as="span" color="brand.500" mt={0.5} flexShrink={0}>
              <FiCheck size={16} />
            </Box>
            {bullet}
          </List.Item>
        ))}
      </List.Root>
    </Box>
  );
}

export default function HomeFeaturesSection() {
  return (
    <Container
      maxW="container.xl"
      py={{ base: 14, md: 20 }}
      id="features"
      scrollMarginTop="5.5rem"
    >
      <Stack gap={6} textAlign="center" mb={10} maxW="3xl" mx="auto">
        <Text
          fontSize="xs"
          fontWeight="bold"
          letterSpacing="0.12em"
          textTransform="uppercase"
          color="brand.600"
        >
          Features
        </Text>
        <Heading
          fontSize={{ base: "2xl", md: "4xl" }}
          letterSpacing="-0.03em"
          lineHeight="1.15"
        >
          Built for teams who live in pipelines, not spreadsheets
        </Heading>
        <Text color="fg.muted" fontSize={{ base: "sm", md: "md" }}>
          From first connection to production schedules—connectors, migrations,
          scheduling, and security in one product surface.
        </Text>
        <Flex gap={3} justify="center" flexWrap="wrap" pt={2}>
          {PLATFORM_PILLARS.map(({ icon: PillIcon, label }) => (
            <Flex
              key={label}
              align="center"
              gap={2}
              px={4}
              py={2}
              borderRadius="full"
              bg="bg.subtle"
              borderWidth="1px"
              borderColor="border.subtle"
              fontSize="sm"
              fontWeight="medium"
              color="gray.700"
            >
              <Box color="brand.600">
                <PillIcon size={16} />
              </Box>
              {label}
            </Flex>
          ))}
        </Flex>
      </Stack>

      <Grid
        templateColumns={{ base: "1fr", lg: "1fr 1fr" }}
        gap={6}
        alignItems="stretch"
      >
        <SimpleGrid columns={1} gap={6}>
          {HOME_FEATURES.slice(0, 2).map((f) => (
            <FeatureCard key={f.title} feature={f} />
          ))}
        </SimpleGrid>
        <SimpleGrid columns={1} gap={6}>
          {HOME_FEATURES.slice(2, 4).map((f) => (
            <FeatureCard key={f.title} feature={f} />
          ))}
        </SimpleGrid>
      </Grid>
    </Container>
  );
}
