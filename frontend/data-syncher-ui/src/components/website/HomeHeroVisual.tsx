import {
  Badge,
  Box,
  Flex,
  Grid,
  HStack,
  Image,
  Stack,
  Text,
} from "@chakra-ui/react";

import { FiActivity, FiCheck, FiRefreshCw } from "react-icons/fi";

import LogoWhite from "@/assets/images/logo-white.svg";
import {
  HERO_DESTINATIONS,
  HERO_MORE_DESTINATIONS,
  HERO_MORE_SOURCES,
  HERO_SOURCES,
} from "@/constants/marketing-connectors";

function ConnectorRow({
  item,
  showCheck,
}: {
  item: { src: string; label: string };
  showCheck?: boolean;
}) {
  return (
    <Flex
      align="center"
      gap={2}
      p={2}
      borderRadius="lg"
      bg="whiteAlpha.100"
      borderWidth="1px"
      borderColor="whiteAlpha.150"
      minH="40px"
    >
      <Image
        src={item.src}
        alt={item.label}
        h="22px"
        w="22px"
        objectFit="contain"
        flexShrink={0}
      />
      <Text
        fontSize="xs"
        color="whiteAlpha.900"
        fontWeight="medium"
        lineClamp={1}
      >
        {item.label}
      </Text>
      {showCheck ? (
        <Box ml="auto" color="brand.300" flexShrink={0}>
          <FiCheck size={14} />
        </Box>
      ) : null}
    </Flex>
  );
}

function MoreChip({ count, noun }: { count: number; noun: string }) {
  if (count <= 0) return null;
  return (
    <Text fontSize="xs" color="whiteAlpha.700" fontWeight="medium" pl={1}>
      +{count} more {noun}
    </Text>
  );
}

export default function HomeHeroVisual() {
  return (
    <Box
      position="relative"
      w="full"
      maxW="540px"
      mx={{ base: "auto", lg: "0" }}
    >
      <Box
        aria-hidden
        position="absolute"
        inset="-8%"
        borderRadius="3xl"
        bg="rgba(45, 212, 191, 0.35)"
        filter="blur(48px)"
        opacity={0.5}
      />
      <Box
        position="relative"
        borderRadius="2xl"
        overflow="hidden"
        borderWidth="1px"
        borderColor="whiteAlpha.300"
        bg="rgba(11, 19, 36, 0.55)"
        backdropFilter="blur(12px)"
        boxShadow="0 32px 64px -20px rgba(0, 0, 0, 0.45)"
      >
        <Flex
          align="center"
          justify="space-between"
          px={4}
          py={3}
          borderBottomWidth="1px"
          borderColor="whiteAlpha.200"
          bg="blackAlpha.300"
        >
          <HStack gap={2}>
            <Image src={LogoWhite} alt="" h="22px" opacity={0.9} />
            <Text fontSize="xs" fontWeight="semibold" color="whiteAlpha.900">
              Pipeline run
            </Text>
          </HStack>
          <Badge colorPalette="green" variant="subtle" size="sm">
            <FiActivity /> Live
          </Badge>
        </Flex>

        <Stack gap={4} p={5}>
          <Text fontSize="xs" color="whiteAlpha.800" textAlign="center">
            Sources → Syncdatesy → destinations (warehouses, lakes &amp; files)
          </Text>
          <Grid templateColumns="1fr auto 1fr" gap={3} alignItems="stretch">
            <Stack gap={2}>
              <Text fontSize="xs" color="whiteAlpha.700" fontWeight="semibold">
                Sources
              </Text>
              {HERO_SOURCES.map((item) => (
                <ConnectorRow key={item.label} item={item} showCheck />
              ))}
              <MoreChip count={HERO_MORE_SOURCES} noun="sources" />
            </Stack>

            <Flex
              direction="column"
              align="center"
              justify="center"
              gap={1}
              px={1}
            >
              <Box color="brand.300">
                <FiRefreshCw size={20} />
              </Box>
              <Text fontSize="xs" color="whiteAlpha.700" textAlign="center">
                Sync
              </Text>
            </Flex>

            <Stack gap={2}>
              <Text fontSize="xs" color="whiteAlpha.700" fontWeight="semibold">
                Destinations
              </Text>
              {HERO_DESTINATIONS.map((item) => (
                <ConnectorRow key={item.label} item={item} />
              ))}
              <MoreChip count={HERO_MORE_DESTINATIONS} noun="targets" />
            </Stack>
          </Grid>

          <Flex
            gap={3}
            flexWrap="wrap"
            pt={2}
            borderTopWidth="1px"
            borderColor="whiteAlpha.150"
          >
            {[
              { label: "2.4M rows", sub: "incremental" },
              { label: "12 tables", sub: "mapped" },
              { label: "99.9%", sub: "success rate" },
            ].map((stat) => (
              <Box
                key={stat.label}
                flex="1"
                minW="90px"
                p={2}
                borderRadius="lg"
                bg="whiteAlpha.80"
                borderWidth="1px"
                borderColor="whiteAlpha.120"
              >
                <Text fontSize="sm" fontWeight="bold" color="brand.200">
                  {stat.label}
                </Text>
                <Text fontSize="xs" color="whiteAlpha.700">
                  {stat.sub}
                </Text>
              </Box>
            ))}
          </Flex>
        </Stack>
      </Box>
    </Box>
  );
}
