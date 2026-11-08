import { Grid } from "@chakra-ui/react";

import { Outlet } from "react-router";

import WebsiteContainer from "@/components/ui/WebsiteContainer";
import Navbar from "@/components/website/Navbar";

export default function WebsiteLayout() {
  return (
    <Grid templateRows="auto 1fr" height="100vh" overflow="hidden">
      <Navbar />
      <WebsiteContainer>
        <Outlet />
      </WebsiteContainer>
    </Grid>
  );
}
