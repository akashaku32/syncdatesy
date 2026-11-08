import { Box, Button, Flex, Image, Text } from "@chakra-ui/react";

import { PiUserCircleLight } from "react-icons/pi";

import { Link, useNavigate } from "react-router";

import Logo from "@/assets/logo.svg";
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
      <Flex justifyContent={"center"} alignItems="center" gap={2}>
        <Image
          src={Logo}
          alt="Logo"
          width={{ base: "40px", md: "50px" }}
          height={{ base: "40px", md: "50px" }}
          onClick={() => navigate(ClientRoutes.HOME)}
          cursor="pointer"
          transition="transform 0.2s"
          _hover={{ transform: "scale(1.05)" }}
        />
        <Text fontSize="2xl" fontWeight="semibold" color="brand.500">
          Datasyncher
        </Text>
      </Flex>
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
