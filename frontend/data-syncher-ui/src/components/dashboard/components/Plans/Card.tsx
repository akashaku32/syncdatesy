import { Flex, Text } from "@chakra-ui/react";

const Card = ({
  name,
  description,
  bulletPoints,
}: {
  name: string;
  description: string;
  bulletPoints: string[];
}) => {
  return (
    <Flex
      borderWidth={1}
      borderRadius="md"
      p={4}
      flexDirection="column"
      gap={2}
    >
      <Text fontSize="lg" fontWeight="bold">
        {name}
      </Text>
      <Text maxW={{ base: "100%", md: "60%" }}>{description}</Text>
      <Flex as="ul" listStyleType="disc" flexDirection="column" pl={4}>
        {bulletPoints.map((point, index) => (
          <Text as="li" key={index}>
            {point}
          </Text>
        ))}
      </Flex>
    </Flex>
  );
};

export default Card;
