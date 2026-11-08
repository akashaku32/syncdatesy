import { useState } from "react";

import { Flex, Input, InputGroup } from "@chakra-ui/react";

import { LuSearch } from "react-icons/lu";

import { useNavigate } from "react-router";

import { getDestinationImage } from "@/components/dashboard/utils/getImage";
import SourceCard from "@/components/shared/SourceCard";
import LoadingSpinner from "@/components/shared/Spinner";
import ClientRoutes from "@/constants/client-routes";
import { VIEW_CONFIG } from "@/constants/view-config";
import useFetchMasterDestinationList from "@/queryOptions/destination/useFetchMasterDestinationList";

const DestinationList = () => {
  const navigate = useNavigate();
  const [searchTerm, setSearchTerm] = useState("");

  const { data, isLoading: isFetchingDestinationsLoading } =
    useFetchMasterDestinationList();

  if (!data) {
    return (
      <Flex>
        <LoadingSpinner />
      </Flex>
    );
  }
  const destinationList = data.content;

  if (isFetchingDestinationsLoading) {
    return <LoadingSpinner />;
  }

  const handleDestionationClick = ({ dst_id }: { dst_id: number }) => {
    // Navigate directly without API call
    navigate(
      `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}/${ClientRoutes.DESTINATION.CONFIGURE}`,
      {
        state: {
          destinationId: dst_id,
          destinationName: destinationList.find((d) => d.dst_id === dst_id)
            ?.name,
        },
      },
    );
  };

  const filteredDestinations = destinationList.filter(({ name }) =>
    name.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  return (
    <Flex
      direction="column"
      alignItems={"center"}
      h="100%"
      justifyContent={"center"}
      gap={VIEW_CONFIG.pageGap}
    >
      <InputGroup endElement={<LuSearch />} maxW="md">
        <Input
          placeholder="Search destination"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </InputGroup>
      <Flex gap={VIEW_CONFIG.pageGap} wrap="wrap" justifyContent="center">
        {filteredDestinations?.map(({ dst_id, name }) => {
          return (
            <SourceCard
              key={dst_id}
              title={name}
              image={getDestinationImage(name)}
              handleClick={() => handleDestionationClick({ dst_id })}
            />
          );
        })}
      </Flex>
    </Flex>
  );
};

export default DestinationList;
