import { Flex } from "@chakra-ui/react";

const TableWrapper = ({ children }: { children: React.ReactNode }) => {
  return (
    <Flex pb={4} h="100%">
      {children}
    </Flex>
  );
};

export default TableWrapper;
