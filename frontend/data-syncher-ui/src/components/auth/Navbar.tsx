import { Flex, GridItem, Image, Text } from "@chakra-ui/react";

import { useNavigate } from "react-router";

import Logo from "@/assets/logo.svg";
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
      <Flex justifyContent="flex-start" alignItems="center" gap={2}>
        <Image
          src={Logo}
          alt="Logo"
          width={{ base: "40px", md: "50px" }}
          height={{ base: "40px", md: "50px" }}
          cursor="pointer"
          transition="transform 0.2s"
          _hover={{ transform: "scale(1.05)" }}
        />
        <Text fontSize="2xl" fontWeight="semibold" color="brand.500">
          Datasyncher
        </Text>
      </Flex>
    </GridItem>
  );
};
export default Navbar;
