import { Grid, useBreakpointValue } from "@chakra-ui/react";

import { Navigate, Outlet } from "react-router";

import Banner from "@/components/auth/Banner";
import Navbar from "@/components/auth/Navbar";
import AuthContainer from "@/components/ui/AuthContainer";
import ClientRoutes from "@/constants/client-routes";
import useAuth from "@/context/Auth/useAuth";

export default function AuthLayout() {
  const {
    authState: { isAuthenticated },
  } = useAuth();

  const gridTemplateAreas = useBreakpointValue({
    base: `"navbar" "main"`,
    md: `"navbar navbar" "banner main"`,
  });

  const gridTemplateColumns = useBreakpointValue({
    base: "1fr",
    md: "1fr 1fr",
  });

  const gridTemplateRows = useBreakpointValue({
    base: "80px 1fr",
    md: "80px 1fr",
  });

  // Redirect to dashboard if already authenticated
  if (isAuthenticated) {
    return <Navigate to={ClientRoutes.DASHBOARD} replace />;
  }

  return (
    <Grid
      templateAreas={gridTemplateAreas}
      templateColumns={gridTemplateColumns}
      templateRows={gridTemplateRows}
      overflow={"hidden"}
      height="100vh"
    >
      <Navbar />
      <Banner />
      <AuthContainer>
        <Outlet />
      </AuthContainer>
    </Grid>
  );
}
