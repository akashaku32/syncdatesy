import { Suspense, useEffect, useState } from "react";

import { Flex } from "@chakra-ui/react";

import { Outlet, useParams } from "react-router";

import LoadingSpinner from "@/components/shared/Spinner";
import useConnectionActivityLogWS from "@/hooks/useConnectionActivityLogWS";
import useConnectionTableStatusWS from "@/hooks/useConnectionTableStatusWS";
import useUpdateSchemaStatusWS from "@/hooks/useUpdateSchemaStatusWS";
import useFetchTableStatus from "@/queryOptions/connector/schema/useFetchTableStatus";
import { useFetchConnectorById } from "@/queryOptions/connector/useFetchConnectorDetailsById";

import Header from "./components/Header";
import Tabs from "./components/Tabs/Tabs";
import { useQueryClient } from "@tanstack/react-query";

const ConnectorDetails = () => {
  const { connectionId } = useParams<{ connectionId: string }>();
  const queryClient = useQueryClient();
  const { data: connector, isLoading } = useFetchConnectorById(
    Number(connectionId) || 0,
  );

  // Initialize WebSockets at the parent level to persist across tabs
  useConnectionTableStatusWS(Number(connectionId));
  useUpdateSchemaStatusWS(Number(connectionId));
  useConnectionActivityLogWS(Number(connectionId));
  useFetchTableStatus(Number(connectionId) || 0);

  const [filterDays, setFilterDays] = useState<number>(2);

  const storageKey = `reloading_tables_${Number(connectionId) || 0}`;
  const [reloadingTables, setReloadingTables] = useState<string[]>(() => {
    try {
      const stored = sessionStorage.getItem(storageKey);
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    sessionStorage.setItem(storageKey, JSON.stringify(reloadingTables));
  }, [reloadingTables, storageKey]);

  useEffect(() => {
    const id = Number(connectionId);
    if (!id) return;
    queryClient.invalidateQueries({
      queryKey: ["SchemaStatus", id],
      refetchType: "active",
    });
  }, [connectionId, queryClient]);

  if (isLoading) {
    return <LoadingSpinner />;
  }

  if (!connector) {
    return <Flex>Connector not found</Flex>;
  }

  return (
    <Flex flexDirection="column" gap={0} h="100%">
      <Flex direction="column" gap={4}>
        <Header connector={connector} />
        <Tabs connector={connector} />
      </Flex>
      <Suspense fallback={<LoadingSpinner />}>
        <Flex overflowX="auto" flexGrow={1} justifyContent={"center"} pt={2}>
          <Outlet
            context={{
              ...connector,
              filterDays,
              setFilterDays,
              reloadingTables,
              setReloadingTables,
            }}
          />
        </Flex>
      </Suspense>
    </Flex>
  );
};

export default ConnectorDetails;
