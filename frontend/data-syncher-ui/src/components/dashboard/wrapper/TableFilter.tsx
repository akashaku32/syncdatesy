import { useRef } from "react";

import { Flex, Input, InputGroup } from "@chakra-ui/react";

import { MdSearch } from "react-icons/md";

interface TableFilterProps {
  handleSearchInputChange?: (
    _event: React.ChangeEvent<HTMLInputElement>,
  ) => void;
  children?: React.ReactNode;
}

const TableFilter = ({
  handleSearchInputChange,
  children,
}: TableFilterProps) => {
  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  const handleDebouncedChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!handleSearchInputChange) return;
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      handleSearchInputChange(e);
    }, 400); // 400ms debounce
  };

  return (
    <Flex>
      <Flex>
        <InputGroup endElement={<MdSearch size={24} />}>
          <Input
            placeholder="Search"
            size="md"
            onChange={handleDebouncedChange}
          />
        </InputGroup>
      </Flex>
      <Flex>{children}</Flex>
    </Flex>
  );
};

export default TableFilter;
