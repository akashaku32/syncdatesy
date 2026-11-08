import { Box, Flex, Grid, GridItem, Link } from "@chakra-ui/react";

import { Link as RouterLink, Navigate, Outlet } from "react-router";

import AuthMarketingPanel from "@/components/auth/AuthMarketingPanel";
import { AppBrand } from "@/components/branding/AppBrand";
import AuthContainer from "@/components/ui/AuthContainer";
import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";

export default function AuthLayout() {
  const {
    authState: { isAuthenticated },
  } = useAuth();

  if (isAuthenticated) {
    return <Navigate to={ClientRoutes.DASHBOARD} replace />;
  }

  return (
    <Grid
      templateAreas={{ base: `"header" "main"`, md: `"banner main"` }}
      templateColumns={{ base: "1fr", md: "minmax(380px, 1.05fr) minmax(400px, 1fr)" }}
      minH="100dvh"
      bg="bg.canvas"
    >
      <GridItem
        area="header"
        as="header"
        display={{ base: "flex", md: "none" }}
        alignItems="center"
        px={4}
        py={3}
        bg="bg.surface"
        borderBottomWidth="1px"
        borderColor="border.subtle"
        zIndex={10}
      >
        <AppBrand size="sm" nameColor="brand.700" />
        <Link
          asChild
          ml="auto"
          fontSize="sm"
          fontWeight="semibold"
          color="brand.600"
          _hover={{ color: "brand.700", textDecoration: "none" }}
        >
          <RouterLink to={ClientRoutes.HOME}>← Back to home</RouterLink>
        </Link>
      </GridItem>

      <GridItem
        area="banner"
        display={{ base: "none", md: "block" }}
        minH="100dvh"
        position="sticky"
        top={0}
        alignSelf="start"
      >
        <AuthMarketingPanel />
      </GridItem>

      <AuthContainer>
        <Flex
          w="full"
          maxW="520px"
          mx="auto"
          justify="flex-end"
          mb={4}
          px={2}
          display={{ base: "none", md: "flex" }}
        >
          <Link
            asChild
            fontSize="sm"
            fontWeight="semibold"
            color="brand.600"
            _hover={{ color: "brand.700", textDecoration: "none" }}
          >
            <RouterLink to={ClientRoutes.HOME}>← Back to home</RouterLink>
          </Link>
        </Flex>
        <Box w="full" maxW="440px" mx="auto">
          <Outlet />
        </Box>
      </AuthContainer>
    </Grid>
  );
}
