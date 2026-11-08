import { Box, Flex, HStack } from "@chakra-ui/react";

import { EMAIL_METADATA_FIELDS } from "../helpers/emailMetadataConstants";

interface MetadataTagSelectorProps {
  onSelectTag: (_tag: string) => void;
  tags?: string[];
  label?: string;
  size?: "xs" | "sm";
}

export const MetadataTagSelector = ({
  onSelectTag,
  tags,
}: MetadataTagSelectorProps) => {
  const displayFields = tags
    ? EMAIL_METADATA_FIELDS.filter(
        (f) => tags.includes(f.tag) || tags.includes(f.id),
      )
    : EMAIL_METADATA_FIELDS;

  return (
    <Flex align="center" gap={1} flexWrap="wrap">
      <HStack gap={1} flexWrap="wrap">
        {displayFields.map((field) => (
          <Box
            key={field.id}
            fontSize="9.5px"
            fontWeight="semibold"
            color="brand.600"
            bg="brand.50"
            px={2}
            py={0.5}
            borderRadius="full"
            cursor="pointer"
            _hover={{ bg: "brand.100", transform: "scale(1.02)" }}
            transition="all 0.15s"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onSelectTag(field.tag);
            }}
            title={`Insert ${field.label} (${field.tag})`}
          >
            +{field.tag.replace(/[{}]/g, "")}
          </Box>
        ))}
      </HStack>
    </Flex>
  );
};

export default MetadataTagSelector;
