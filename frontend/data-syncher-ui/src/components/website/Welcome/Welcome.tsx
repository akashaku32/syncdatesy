import { Button, Flex, Text } from "@chakra-ui/react";

import { GoArrowRight } from "react-icons/go";

const Welcome = () => {
  return (
    <Flex
      justifyContent="center"
      alignItems="center"
      flexDirection={"column"}
      gap={4}
    >
      <Flex>
        <Text fontWeight="bold" fontSize="3xl" mr={1}>
          Welcome to
        </Text>
        <Text fontWeight="bold" fontSize="3xl" color="brand.500">
          Data Syncher
        </Text>
      </Flex>
      <Text textAlign={"center"} fontSize="md" maxW="600px">
        Managing and syncing data across platforms has never been easier.
        Datasyncher simplifies your data migration, ETL processes, and real-time
        synchronization so your business can focus on what truly matters.
      </Text>
      <Button colorPalette="brand">
        Get Started <GoArrowRight />
      </Button>
    </Flex>
  );
};

export default Welcome;
