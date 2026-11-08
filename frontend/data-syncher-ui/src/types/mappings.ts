export type TableMapping = {
  sourceTable: string;
  destinationTable: string;
  status?: string;
};

export type TableMappingDTO = {
  source_table?: string;
  destination_table?: string;
  status?: string;
};
