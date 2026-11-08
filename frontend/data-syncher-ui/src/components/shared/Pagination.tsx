import { Flex, IconButton, Text } from "@chakra-ui/react";

import { IoChevronBack, IoChevronForward } from "react-icons/io5";

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (_page: number) => void;
}

const Pagination = ({
  currentPage,
  totalPages,
  onPageChange,
}: PaginationProps) => {
  return (
    <Flex gap={4} alignItems="center" justifyContent="center" mt={4}>
      <IconButton
        aria-label="Previous page"
        variant="ghost"
        size="sm"
        disabled={currentPage === 1}
        onClick={() => onPageChange(currentPage - 1)}
      >
        <IoChevronBack />
      </IconButton>

      <Text fontSize="sm">
        Page {currentPage} of {totalPages}
      </Text>

      <IconButton
        aria-label="Next page"
        variant="ghost"
        size="sm"
        disabled={currentPage === totalPages}
        onClick={() => onPageChange(currentPage + 1)}
      >
        <IoChevronForward />
      </IconButton>
    </Flex>
  );
};

export default Pagination;
