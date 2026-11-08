import { type ReactNode } from "react";

import { Box, Heading, Stack, Text } from "@chakra-ui/react";

import { AppBrand } from "@/components/branding/AppBrand";

type AuthFormCardProps = {
  title: string;
  description?: ReactNode;
  children: ReactNode;
};

export function AuthFormCard({
  title,
  description,
  children,
}: AuthFormCardProps) {
  return (
    <Box
      w="full"
      maxW="440px"
      bg="bg.surface"
      borderRadius="2xl"
      boxShadow="0 24px 48px -12px rgba(15, 23, 42, 0.18)"
      borderWidth="1px"
      borderColor="border.subtle"
      p={{ base: 6, md: 8 }}
    >
      <AppBrand size="md" mb={6} />
      <Stack gap={2} mb={6}>
        <Heading
          as="h1"
          fontSize={{ base: "2xl", md: "3xl" }}
          fontWeight="bold"
        >
          {title}
        </Heading>
        {description ? (
          <Text fontSize="sm" color="gray.600" lineHeight="tall">
            {description}
          </Text>
        ) : null}
      </Stack>
      {children}
    </Box>
  );
}
