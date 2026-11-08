import { Flex } from "@chakra-ui/react";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";
import ClientRoutes from "@/constants/client-routes";
import { VIEW_CONFIG } from "@/constants/view-config";

import DestinationList from "./components/DestinationList";

const NewDestination = () => {
  return (
    <Flex direction="column" gap={VIEW_CONFIG.pageGap}>
      <PageHeader
        breadcrumbs={[
          {
            label: "Destinations",
            route: `${ClientRoutes.DASHBOARD}/${ClientRoutes.DESTINATION.ROOT}`,
          },
          { label: "Add destination" },
        ]}
        title="Add destination"
        subtitle="A destination is a data warehouse that store all your data"
      />
      <DestinationList />
    </Flex>
  );
};
export default NewDestination;
