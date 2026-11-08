import { Button, Flex, Text } from "@chakra-ui/react";

import { GoArrowRight } from "react-icons/go";

import { APP_NAME, APP_TAGLINE } from "@/constants/branding";

const Welcome = () => {
  return (
    <Flex
      justifyContent="center"
      alignItems="center"
      flexDirection={"column"}
      gap={4}
    >
      <Text fontWeight="bold" fontSize="3xl" color="brand.600">
        Welcome to {APP_NAME}
      </Text>
      <Text textAlign="center" fontSize="md" maxW="600px" color="gray.600">
        {APP_TAGLINE}
      </Text>
      <Button colorPalette="brand">
        Get Started <GoArrowRight />
      </Button>
    </Flex>
  );
};

export default Welcome;
