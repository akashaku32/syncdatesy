import { Flex, Image, Text } from "@chakra-ui/react";

import NoDestinationIllustration from "@/assets/images/destination-empty.svg";
import { VIEW_CONFIG } from "@/constants/view-config";

const NoDestinations = () => {
  return (
    <Flex
      h="100%"
      justifyContent="center"
      alignItems="center"
      flexDirection="column"
      gap={VIEW_CONFIG.pageGap}
    >
      <Image src={NoDestinationIllustration} alt="No destinations" />
      <Flex textAlign={"center"} flexDirection={"column"} gap={2}>
        <Text fontWeight="bold">
          You do not have any destinations in your account
        </Text>
        <Text>Please add a destination to your account</Text>
      </Flex>
    </Flex>
  );
};

export default NoDestinations;
