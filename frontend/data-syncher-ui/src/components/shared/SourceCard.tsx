import { Flex, Image, Text } from "@chakra-ui/react";

import { VIEW_CONFIG } from "@/constants/view-config";

import LoadingSpinner from "./Spinner";

const SourceCard = ({
  title,
  image,
  handleClick,
  isLoading = false,
  isSelected,
}: {
  title: string;
  image: string;
  handleClick: () => void;
  isLoading?: boolean;
  isSelected?: boolean;
}) => {
  return (
    <Flex
      direction="column"
      boxShadow={"sm"}
      p={6}
      borderRadius="md"
      alignItems="center"
      justifyContent={"center"}
      gap={VIEW_CONFIG.pageGap}
      flexWrap={"wrap"}
      cursor={isLoading ? "not-allowed" : "pointer"}
      _hover={!isLoading ? { boxShadow: "lg" } : {}}
      onClick={isLoading ? undefined : handleClick}
      position="relative"
      opacity={isLoading ? 0.6 : 1}
      border={isSelected ? "2px solid" : "none"}
      width="200px"
      minH="150x"
    >
      <Image src={image} alt={title} h={16} />
      <Text fontSize="sm" wordBreak={"break-word"} textAlign="center">
        {title}
      </Text>
      {isLoading && <LoadingSpinner position="absolute" />}
    </Flex>
  );
};
export default SourceCard;
