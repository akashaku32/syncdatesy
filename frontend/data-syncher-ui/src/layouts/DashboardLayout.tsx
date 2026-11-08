import { useState } from "react";

import { Grid, GridItem } from "@chakra-ui/react";

import { Outlet, useLocation, useParams } from "react-router";

import Header from "@/components/dashboard/components/Header";
import Sidebar from "@/components/dashboard/components/Sidebar/Sidebar";
import ClientRoutes from "@/constants/client-routes";
import useConnectionTableStatusWS from "@/hooks/useConnectionTableStatusWS";

export default function Layout() {
  const { connectionId } = useParams<{ connectionId: string }>();
  // Keep WebSocket open for any route providing a connectionId (e.g. /connectors/:connectionId/...)
  useConnectionTableStatusWS(Number(connectionId) || null);

  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const sidebarWidth = isSidebarCollapsed ? "0" : "250px";
  const toggleSidebar = () => setIsSidebarCollapsed(!isSidebarCollapsed);
  const location = useLocation();
  const isSupportRoute = location.pathname.startsWith(ClientRoutes.SUPPORT);

  return (
    <Grid
      templateAreas={{
        base: `"main"`,
        md: `"sidebar header" "sidebar main"`,
      }}
      gridTemplateRows={{ base: "1fr", md: "60px 1fr" }}
      gridTemplateColumns={{ base: "1fr", md: `${sidebarWidth} 1fr` }}
      height="100vh"
      transition="grid-template-columns 0.3s ease-in-out"
    >
      <Sidebar sidebarWidth={sidebarWidth} isDrawer={false} />
      <Header
        sidebarWidth={sidebarWidth}
        onSidebarToggle={toggleSidebar}
        isSidebarCollapsed={isSidebarCollapsed}
      />
      <GridItem
        area="main"
        p={6}
        boxSizing="border-box"
        overflowY={isSupportRoute ? "hidden" : "auto"}
        height="calc(100vh - 60px)"
        bg="gray.50"
        mt={{ base: "60px", md: "0" }}
        transition="all 0.3s ease-in-out"
      >
        <Outlet />
      </GridItem>
    </Grid>
  );
}
