import { GridItem } from "@chakra-ui/react";

import { useNavigate } from "react-router";

import { AppBrand } from "@/components/branding/AppBrand";
import ClientRoutes from "@/constants/client-routes";

const Navbar = () => {
  const navigate = useNavigate();

  return (
    <GridItem
      area="header"
      as="nav"
      bg="white"
      boxShadow="md"
      position="fixed"
      top={0}
      left={0}
      right={0}
      zIndex={1000}
      justifyContent="space-between"
      alignItems="center"
      padding={4}
      onClick={() => navigate(ClientRoutes.HOME)}
      cursor={"pointer"}
    >
      <AppBrand size="lg" />
    </GridItem>
  );
};
export default Navbar;
