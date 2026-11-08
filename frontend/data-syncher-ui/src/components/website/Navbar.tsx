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
    >
      <AppBrand
        size="lg"
        onClick={() => navigate(ClientRoutes.HOME)}
      />
      <Box display="flex" gap={4}>
        <Link to="#home">Home</Link>
        <Link to="#features">Features</Link>
        <Link to="#about">About</Link>
        <Link to="#contact">Contact</Link>
      </Box>
      <Flex gap={4}>
        <Button
          onClick={() => navigate(`${ClientRoutes.AUTH}/${ClientRoutes.LOGIN}`)}
          colorPalette="brand"
        >
          <PiUserCircleLight />
          Login
        </Button>
        <Button
          onClick={() =>
            navigate(`${ClientRoutes.AUTH}/${ClientRoutes.REGISTER}`)
          }
          variant="ghost"
        >
          Sign Up
        </Button>
      </Flex>
    </Flex>
  );
};
export default Navbar;
