export interface EmailMetadataOption {
  id: string;
  tag: string;
  label: string;
}

export const EMAIL_METADATA_FIELDS: EmailMetadataOption[] = [
  {
    id: "table",
    tag: "{table}",
    label: "Table Name",
  },
  {
    id: "destination",
    tag: "{destination}",
    label: "Destination",
  },
  {
    id: "connection",
    tag: "{connection}",
    label: "Connection",
  },
  {
    id: "company",
    tag: "{company}",
    label: "Company",
  },
  {
    id: "file",
    tag: "{file}",
    label: "File",
  },
  {
    id: "path",
    tag: "{path}",
    label: "Destination Path",
  },
  {
    id: "timestamp",
    tag: "{timestamp}",
    label: "Completion Timestamp",
  },
];
