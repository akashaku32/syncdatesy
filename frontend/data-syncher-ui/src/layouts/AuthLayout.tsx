import { Grid, GridItem, useBreakpointValue } from "@chakra-ui/react";

import { Link, Navigate, Outlet } from "react-router";

import AuthMarketingPanel from "@/components/auth/AuthMarketingPanel";
import { AppBrand } from "@/components/branding/AppBrand";
import AuthContainer from "@/components/ui/AuthContainer";
import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";

export default function AuthLayout() {
  const {
    authState: { isAuthenticated },
  } = useAuth();

  const gridTemplateAreas = useBreakpointValue({
    base: `"header" "main"`,
    md: `"header header" "banner main"`,
  });

  const gridTemplateColumns = useBreakpointValue({
    base: "1fr",
    md: "1.05fr 1fr",
  });

  const gridTemplateRows = useBreakpointValue({
    base: "auto 1fr",
    md: "72px 1fr",
  });

  if (isAuthenticated) {
    return <Navigate to={ClientRoutes.DASHBOARD} replace />;
  }

  return (
    <Grid
      templateAreas={gridTemplateAreas}
      templateColumns={gridTemplateColumns}
      templateRows={gridTemplateRows}
      minH="100vh"
      bg="gray.50"
    >
      <GridItem
        area="header"
        as="header"
        display="flex"
        alignItems="center"
        px={{ base: 4, md: 6 }}
        bg="white"
        borderBottomWidth="1px"
        borderColor="gray.100"
        boxShadow="sm"
        zIndex={10}
      >
        <AppBrand size="md" nameColor="brand.600" />
        <Link
          to={ClientRoutes.HOME}
          style={{
            marginLeft: "auto",
            fontSize: "0.875rem",
            fontWeight: 600,
            color: "var(--ck-colors-brand-600)",
          }}
        >
          ← Back to home
        </Link>
      </GridItem>
      <GridItem area="banner" display={{ base: "none", md: "block" }} minH={0}>
        <AuthMarketingPanel />
      </GridItem>
      <AuthContainer>
        <Outlet />
      </AuthContainer>
    </Grid>
  );
}
