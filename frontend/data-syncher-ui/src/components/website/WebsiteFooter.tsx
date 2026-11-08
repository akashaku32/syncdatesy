import { Box, Container, Flex, Link, Stack, Text } from "@chakra-ui/react";

import { Link as RouterLink } from "react-router";

import { AppBrand } from "@/components/branding/AppBrand";
import ClientRoutes from "@/constants/client-routes";

import HomeSectionLink from "./HomeSectionLink";

export default function WebsiteFooter() {
  return (
    <Box as="footer" bg="navy.950" color="whiteAlpha.800" py={12}>
      <Container maxW="container.xl">
        <Flex
          direction={{ base: "column", md: "row" }}
          gap={10}
          justify="space-between"
          align={{ base: "flex-start", md: "center" }}
        >
          <Stack gap={3}>
            <AppBrand size="sm" nameColor="white" />
            <Text fontSize="sm" maxW="xs" color="whiteAlpha.700">
              Syncdatesy keeps pipelines, migrations, and connectors aligned in one workspace.
            </Text>
          </Stack>
          <Stack gap={2} fontSize="sm">
            <HomeSectionLink sectionId="features">Features</HomeSectionLink>
            <HomeSectionLink sectionId="about">How it works</HomeSectionLink>
            <HomeSectionLink sectionId="contact">Get started</HomeSectionLink>
          </Stack>
          <Stack gap={2} fontSize="sm">
            <Link asChild color="brand.300" _hover={{ color: "brand.200" }}>
              <RouterLink to={`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`}>
                Sign in
              </RouterLink>
            </Link>
            <Link asChild color="brand.300" _hover={{ color: "brand.200" }}>
              <RouterLink to={`${ClientRoutes.AUTH}/${ClientRoutes.REGISTER}`}>
                Request access
              </RouterLink>
            </Link>
          </Stack>
        </Flex>
        <Text mt={10} fontSize="xs" color="whiteAlpha.600" textAlign="center">
          © {new Date().getFullYear()} Syncdatesy. Built for data teams who care about reliability.
        </Text>
      </Container>
    </Box>
  );
}
