import { useState } from "react";

import { Button, Flex, Input, InputGroup } from "@chakra-ui/react";

import { IoMdArrowBack } from "react-icons/io";
import { LuSearch } from "react-icons/lu";

import { getSourceImage } from "@/components/dashboard/utils/getImage";
import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import SourceCard from "@/components/shared/SourceCard";
import LoadingSpinner from "@/components/shared/Spinner";
import ClientRoutes from "@/constants/client-routes";
import { VIEW_CONFIG } from "@/constants/view-config";
import useFetchMasterSourceList from "@/queryOptions/useFetchMasterSourceList";

const Source = ({
  selectedSource,
  onSourceSelect,
  handlePrevious,
}: {
  selectedSource: string | null;
  onSourceSelect: (_source: string) => void;
  handlePrevious: () => void;
}) => {
  const [searchTerm, setSearchTerm] = useState("");
  const { data: sourceList, isLoading } = useFetchMasterSourceList();

  const filteredSourceList = sourceList?.filter(({ name }) =>
    name.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  // Manual mapping for when the API returns display names but requires internal names
  const DISPLAY_TO_API_NAME_MAP: Record<string, string> = {
    "Microsoft Dynamics 365 F&O": "MicrosoftDynamics365_FO",
    "Amazon S3": "AmazonS3",
    NetSuite: "NetSuite",
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
        title="Select source type"
        subtitle="Select your source type you want to create your connectors from"
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
            placeholder="Search sources"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </InputGroup>
        {isLoading && <LoadingSpinner />}
        {!isLoading && (
          <Flex gap={VIEW_CONFIG.pageGap} wrap="wrap" justifyContent="center">
            {filteredSourceList?.map(({ name, src_id, source_name }) => {
              const sourceIdentifier =
                source_name || DISPLAY_TO_API_NAME_MAP[name] || name;
              const isSelected = selectedSource === sourceIdentifier;
              return (
                <SourceCard
                  key={src_id}
                  title={name}
                  image={getSourceImage(name)}
                  handleClick={() => onSourceSelect(sourceIdentifier)}
                  isSelected={isSelected}
                />
              );
            })}
          </Flex>
        )}
      </Flex>
      <Button alignSelf="center" variant="outline" onClick={handlePrevious}>
        <IoMdArrowBack />
        Back
      </Button>
    </Flex>
  );
};
export default Source;
