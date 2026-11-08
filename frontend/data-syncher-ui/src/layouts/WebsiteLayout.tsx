import { Grid } from "@chakra-ui/react";

import { Outlet } from "react-router";

import WebsiteContainer from "@/components/ui/WebsiteContainer";
import Navbar from "@/components/website/Navbar";

export default function WebsiteLayout() {
  return (
    <Grid templateRows="auto 1fr" minH="100vh">
      <Navbar />
      <WebsiteContainer>
        <Outlet />
      </WebsiteContainer>
    </Grid>
  );
}
