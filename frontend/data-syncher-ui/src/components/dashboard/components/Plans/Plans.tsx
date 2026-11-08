import { Flex, Grid, Text } from "@chakra-ui/react";

import PageHeader from "@/components/dashboard/wrapper/PageHeader";

import Card from "./Card";
import PlanTable from "./Table";
import { CardData, Description } from "./tableData";

const Plans = () => {
  return (
    <Flex flexDirection="column" height="100%" gap={8}>
      <PageHeader
        breadcrumbs={[
          {
            label: "Plans",
            route: "",
          },
        ]}
        title="Plans"
      />
      <Flex alignItems="center" flexDirection="column" gap={4}>
        <Text fontSize="2xl" fontWeight="bold">
          Pay as per your usage
        </Text>
        <Text textAlign="center" maxWidth={{ base: "100%", md: "60%" }}>
          {Description}
        </Text>
      </Flex>
      <Grid gridTemplateColumns="repeat(3, 1fr)" gap={4}>
        {CardData.map((card) => (
          <Card key={card.id} {...card} />
        ))}
      </Grid>
      <PlanTable />
    </Flex>
  );
};

export default Plans;
