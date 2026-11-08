import { useState } from "react";

import { Flex, Input, InputGroup } from "@chakra-ui/react";

import { LuSearch } from "react-icons/lu";

import { getDestinationImage } from "@/components/dashboard/utils/getImage";
import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import SourceCard from "@/components/shared/SourceCard";
import LoadingSpinner from "@/components/shared/Spinner";
import ClientRoutes from "@/constants/client-routes";
import { VIEW_CONFIG } from "@/constants/view-config";
import useFetchAllUserCreatedDestinationList from "@/queryOptions/destination/useFetchAllUserCreatedDestinationList";
import useSelectDestination from "@/queryOptions/destination/useSelectDestination";

const Destination = ({
  selectedDestination,
  onDestinationSelect,
}: {
  selectedDestination: string | null;
  onDestinationSelect: (_destination: {
    name: string;
    dst_config_id: number;
  }) => void;
}) => {
  const [searchTerm, setSearchTerm] = useState("");
  const { data: destinationList, isLoading } =
    useFetchAllUserCreatedDestinationList();

  const {
    mutate: selectDestination,
    isPending: isSelectingDestinationPending,
  } = useSelectDestination();

  const filteredDestinations = destinationList?.filter(({ name }) =>
    name.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  const handleDestionationClick = ({
    dst_config_id,
    name,
  }: {
    dst_config_id: number;
    name: string;
  }) => {
    // Prevent multiple clicks while a destination is being selected
    if (isSelectingDestinationPending) return;
    selectDestination(
      { destination: dst_config_id },
      {
        onSuccess: () => {
          onDestinationSelect({ name, dst_config_id });
        },
      },
    );
  };

  return (
    <Flex direction="column" gap={VIEW_CONFIG.pageGap}>
      <PageHeader
        breadcrumbs={[
          {
            label: "Connector",
            route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.CONNECTORS.ROOT}`,
          },
          { label: "Configure" },
        ]}
        title="Select Destination"
        subtitle="Select an existing destination or add a new one"
      />
      <Flex
        direction="column"
        alignItems={"center"}
        h="100%"
        justifyContent={"center"}
        gap={VIEW_CONFIG.pageGap}
      >
        <InputGroup endElement={<LuSearch />} maxW="md">
          <Input
            placeholder="Search destinations"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </InputGroup>
        {isLoading && <LoadingSpinner />}
        {!isLoading && (
          <Flex gap={VIEW_CONFIG.pageGap} wrap="wrap" justifyContent="center">
            {filteredDestinations?.map(({ dst, name, dst_config_id }) => {
              const isSelected = name === selectedDestination;

              return (
                <SourceCard
                  key={dst_config_id}
                  title={name}
                  image={getDestinationImage(dst)}
                  isSelected={isSelected}
                  handleClick={() =>
                    handleDestionationClick({ dst_config_id, name })
                  }
                  isLoading={isSelectingDestinationPending && isSelected}
                />
              );
            })}
          </Flex>
        )}
      </Flex>
    </Flex>
  );
};
export default Destination;
