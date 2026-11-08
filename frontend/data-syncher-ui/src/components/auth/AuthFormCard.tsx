import { type ReactNode } from "react";

import { Box, Heading, Separator, Stack, Text } from "@chakra-ui/react";

type AuthFormCardProps = {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  eyebrow?: string;
};

export function AuthFormCard({
  title,
  description,
  children,
  eyebrow,
}: AuthFormCardProps) {
  return (
    <Box
      w="full"
      bg="bg.surface"
      borderRadius="2xl"
      boxShadow="0 4px 24px -4px rgba(15, 23, 42, 0.08), 0 0 0 1px rgba(15, 23, 42, 0.04)"
      borderWidth="1px"
      borderColor="border.subtle"
      p={{ base: 6, md: 8 }}
    >
      {eyebrow ? (
        <Text
          fontSize="xs"
          fontWeight="bold"
          letterSpacing="0.12em"
          textTransform="uppercase"
          color="brand.600"
          mb={2}
        >
          {eyebrow}
        </Text>
      ) : null}
      <Stack gap={2} mb={6}>
        <Heading
          as="h1"
          fontSize={{ base: "2xl", md: "2.25rem" }}
          fontWeight="bold"
          letterSpacing="-0.02em"
          lineHeight="1.15"
        >
          {title}
        </Heading>
        {description ? (
          <Text fontSize="sm" color="fg.muted" lineHeight="tall">
            {description}
          </Text>
        ) : null}
      </Stack>
      <Separator mb={6} borderColor="border.subtle" />
      {children}
    </Box>
  );
}
