/**
 * Curated connector logos for marketing (aligned with dashboard getImage mappings).
 * Sources = operational systems you extract from; destinations = warehouses, lakes, files.
 */

import amazonS3 from "@/assets/images/amazon-s3.svg";
import azureDataLake from "@/assets/images/azure-data-lake-storage.svg";
import databricks from "@/assets/images/databricks.svg";
import googleDrive from "@/assets/images/google-drive.svg";
import msDynamics from "@/assets/images/ms-dynamics.svg";
import postgres from "@/assets/images/postgres.svg";
import salesforce from "@/assets/images/salesforce.svg";
import sapOdata from "@/assets/images/sap_odata.svg";
import sftp from "@/assets/images/sftp.svg";
import sharepoint from "@/assets/images/sharepoint.svg";
import snowflake from "@/assets/images/snowflake.svg";
import mysql from "@/assets/images/sql.svg";

export type MarketingConnector = {
  src: string;
  label: string;
};

/** Typical extract sources (CRM, ERP, databases, reviews). */
export const MARKETING_SOURCES: MarketingConnector[] = [
  { src: salesforce, label: "Salesforce" },
  { src: sapOdata, label: "SAP OData" },
  { src: msDynamics, label: "Dynamics 365" },
  { src: postgres, label: "PostgreSQL" },
  { src: mysql, label: "MySQL" },
];

/** Warehouses, lakes, and file-based landing zones. */
export const MARKETING_DESTINATIONS: MarketingConnector[] = [
  { src: snowflake, label: "Snowflake" },
  { src: databricks, label: "Databricks" },
  { src: amazonS3, label: "Amazon S3" },
  { src: azureDataLake, label: "Azure Data Lake" },
  { src: sharepoint, label: "SharePoint" },
  { src: sftp, label: "SFTP" },
  { src: googleDrive, label: "Google Drive" },
];

/** Hero card: compact subset so the layout stays readable. */
export const HERO_SOURCES = MARKETING_SOURCES.slice(0, 3);
export const HERO_DESTINATIONS = MARKETING_DESTINATIONS.slice(0, 4);

export const HERO_MORE_SOURCES = MARKETING_SOURCES.length - HERO_SOURCES.length;
export const HERO_MORE_DESTINATIONS =
  MARKETING_DESTINATIONS.length - HERO_DESTINATIONS.length;

/** Full logo strip on the home page. */
export const LOGO_STRIP_CONNECTORS: MarketingConnector[] = [
  ...MARKETING_SOURCES,
  ...MARKETING_DESTINATIONS.filter(
    (d) => !MARKETING_SOURCES.some((s) => s.label === d.label),
  ),
];
