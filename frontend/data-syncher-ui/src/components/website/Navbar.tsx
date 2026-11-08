import { Box, Button, Flex } from "@chakra-ui/react";

import { PiUserCircleLight } from "react-icons/pi";

import { Link, useNavigate } from "react-router";

import { AppBrand } from "@/components/branding/AppBrand";
import ClientRoutes from "@/constants/client-routes";

const Navbar = () => {
  const navigate = useNavigate();

  return (
    <Flex
      as="nav"
      bg="bg.surface"
      backdropFilter="blur(8px)"
      borderBottomWidth="1px"
      borderColor="border.subtle"
      boxShadow="sm"
      position="fixed"
      top={0}
      left={0}
      right={0}
      zIndex={1000}
      justifyContent="space-between"
      alignItems="center"
      padding={4}
    >
      <AppBrand size="lg" onClick={() => navigate(ClientRoutes.HOME)} />
      <Box
        display={{ base: "none", md: "flex" }}
        gap={6}
        fontSize="sm"
        fontWeight="medium"
      >
        <Link to="/#home">Home</Link>
        <Link to="/#features">Features</Link>
        <Link to="/#about">How it works</Link>
        <Link to="/#contact">Get started</Link>
      </Box>
      <Flex gap={4}>
        <Button
          onClick={() => navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)}
          colorPalette="brand"
        >
          <PiUserCircleLight />
          Sign in
        </Button>
        <Button
          onClick={() =>
            navigate(`${ClientRoutes.AUTH}/${ClientRoutes.REGISTER}`)
          }
          variant="outline"
          colorPalette="brand"
        >
          Request access
        </Button>
      </Flex>
    </Flex>
  );
};
export default Navbar;
