import { Box, Container, Flex, Heading, Stack, Text } from "@chakra-ui/react";

import { FiArrowRight } from "react-icons/fi";

import { HOME_STEPS } from "@/constants/home-content";

export default function HomeHowItWorksSection() {
  return (
    <Box
      id="about"
      bg="bg.subtle"
      py={{ base: 14, md: 20 }}
      scrollMarginTop="5.5rem"
      position="relative"
      overflow="hidden"
    >
      <Box
        aria-hidden
        position="absolute"
        top="50%"
        left="50%"
        transform="translate(-50%, -50%)"
        w="800px"
        h="400px"
        borderRadius="full"
        bgGradient="radial(circle, brand.100, transparent 70%)"
        opacity={0.4}
      />
      <Container maxW="container.xl" position="relative">
        <Stack
          gap={3}
          textAlign="center"
          mb={{ base: 10, md: 14 }}
          maxW="2xl"
          mx="auto"
        >
          <Text
            fontSize="xs"
            fontWeight="bold"
            letterSpacing="0.12em"
            textTransform="uppercase"
            color="brand.600"
          >
            How it works
          </Text>
          <Heading
            fontSize={{ base: "2xl", md: "4xl" }}
            letterSpacing="-0.03em"
            lineHeight="1.15"
          >
            From connection to continuous sync
          </Heading>
          <Text color="fg.muted">
            Three stages your data team already thinks in—wired together in
            Syncdatesy.
          </Text>
        </Stack>

        <Stack gap={0} display={{ base: "flex", lg: "none" }}>
          {HOME_STEPS.map((s, index) => {
            const Icon = s.icon;
            return (
              <Box
                key={s.step}
                position="relative"
                pb={index < HOME_STEPS.length - 1 ? 8 : 0}
              >
                {index < HOME_STEPS.length - 1 ? (
                  <Box
                    position="absolute"
                    left="27px"
                    top="56px"
                    bottom={0}
                    w="2px"
                    bg="brand.200"
                  />
                ) : null}
                <StepCard step={s} Icon={Icon} />
              </Box>
            );
          })}
        </Stack>

        <Flex
          display={{ base: "none", lg: "flex" }}
          align="stretch"
          gap={0}
          justify="space-between"
        >
          {HOME_STEPS.map((s, index) => {
            const Icon = s.icon;
            return (
              <Flex key={s.step} flex="1" align="stretch" minW={0}>
                <Box flex="1" px={3}>
                  <StepCard step={s} Icon={Icon} />
                </Box>
                {index < HOME_STEPS.length - 1 ? (
                  <Flex align="center" px={2} color="brand.400" flexShrink={0}>
                    <FiArrowRight size={28} />
                  </Flex>
                ) : null}
              </Flex>
            );
          })}
        </Flex>
      </Container>
    </Box>
  );
}

function StepCard({
  step,
  Icon,
}: {
  step: (typeof HOME_STEPS)[number];
  Icon: (typeof HOME_STEPS)[number]["icon"];
}) {
  return (
    <Box
      bg="bg.surface"
      borderRadius="2xl"
      borderWidth="1px"
      borderColor="border.subtle"
      p={6}
      h="full"
      boxShadow="sm"
      transition="box-shadow 0.2s, border-color 0.2s"
      _hover={{
        borderColor: "brand.200",
        boxShadow: "0 16px 40px -24px rgba(13, 148, 136, 0.35)",
      }}
    >
      <Flex align="flex-start" gap={4} mb={4}>
        <Flex direction="column" align="center" gap={2} flexShrink={0}>
          <Flex
            w={14}
            h={14}
            borderRadius="full"
            bg="brand.600"
            color="white"
            align="center"
            justify="center"
            boxShadow="0 8px 20px -6px rgba(13, 148, 136, 0.5)"
          >
            <Icon size={22} />
          </Flex>
          <Text fontSize="xs" fontWeight="bold" color="brand.600">
            {step.step}
          </Text>
        </Flex>
        <Box pt={1}>
          <Heading size="md" mb={2} letterSpacing="-0.02em" lineHeight="short">
            {step.title}
          </Heading>
        </Box>
      </Flex>
      <Text fontSize="sm" color="fg.muted" lineHeight="tall" mb={4}>
        {step.body}
      </Text>
      <Text
        fontSize="xs"
        fontWeight="semibold"
        textTransform="uppercase"
        letterSpacing="0.08em"
        color="brand.700"
      >
        Outcome
      </Text>
      <Text fontSize="sm" fontWeight="medium" color="gray.800" mt={1}>
        {step.outcome}
      </Text>
    </Box>
  );
}
