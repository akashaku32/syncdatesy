import { Grid } from "@chakra-ui/react";

import { Outlet } from "react-router";

import WebsiteContainer from "@/components/ui/WebsiteContainer";
import Navbar from "@/components/website/Navbar";
import WebsiteFooter from "@/components/website/WebsiteFooter";

export default function WebsiteLayout() {
  return (
    <Grid templateRows="auto 1fr auto" minH="100dvh">
      <Navbar />
      <WebsiteContainer>
        <Outlet />
      </WebsiteContainer>
      <WebsiteFooter />
    </Grid>
  );
}
