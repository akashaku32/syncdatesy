import AmazonS3Illustration from "@/assets/images/amazon-s3.svg";
import AzureDataLakeStorageIllustration from "@/assets/images/azure-data-lake-storage.svg";
import DataBricksIllustration from "@/assets/images/databricks.svg";
import GoogleDriveIllustration from "@/assets/images/google-drive.svg";
import GoogleReviewIllustration from "@/assets/images/google-review.svg";
import MicrosoftDynamicsIllustration from "@/assets/images/ms-dynamics.svg";
import PostgreSQLIllustration from "@/assets/images/postgres.svg";
import SalesforceSandboxIllustration from "@/assets/images/salesforce-sandbox.svg";
import SalesForceIllustration from "@/assets/images/salesforce.svg";
import SAPODataIllustration from "@/assets/images/sap_odata.svg";
import SFTPIllustration from "@/assets/images/sftp.svg";
import SharepointIllustration from "@/assets/images/sharepoint.svg";
import SnowFlakeIllustration from "@/assets/images/snowflake.svg";
import MySQLIllustration from "@/assets/images/sql.svg";

// Image mapping configuration
const IMAGE_MAP = {
  // Sources
  snowflake: SnowFlakeIllustration,
  microsoftdynamics365_fo: MicrosoftDynamicsIllustration,
  databricks: DataBricksIllustration,
  postgresql: PostgreSQLIllustration,
  mysql: MySQLIllustration,
  amazons3: AmazonS3Illustration,
  googlereviews: GoogleReviewIllustration,
  sapodata: SAPODataIllustration,
  sap_odata: SAPODataIllustration,
  sap: SAPODataIllustration,
  netsuite: MicrosoftDynamicsIllustration,
  oracle_netsuite: MicrosoftDynamicsIllustration,
  // File-based sources (also valid as destinations)
  sftp: SFTPIllustration,
  googledrive: GoogleDriveIllustration,
  azuredatalakestorage: AzureDataLakeStorageIllustration,

  // Destinations
  salesforce: SalesForceIllustration,
  salesforcesandbox: SalesforceSandboxIllustration,
  sharepoint: SharepointIllustration,
} as const;

// Default fallback image
const DEFAULT_IMAGE = DataBricksIllustration;

/**
 * Get the appropriate image for a given source/destination name
 * @param name - The name of the source or destination
 * @param fallback - Optional fallback image (defaults to DataBricks)
 * @returns The image asset
 */
export const getSourceImage = (name: string, fallback?: string): string => {
  if (!name) return fallback || DEFAULT_IMAGE;

  // Normalize the name: lowercase, remove spaces, hyphens, dots
  const normalizedName = name.toLowerCase().replace(/[\s\-.]/g, "");

  // Try exact match first
  if (normalizedName in IMAGE_MAP) {
    return IMAGE_MAP[normalizedName as keyof typeof IMAGE_MAP];
  }

  // Try partial matches for common variations (check more specific matches first)
  // Check for sandbox first (more specific) before salesforce (less specific)

  const partialMatches = {
    snowflake: SnowFlakeIllustration,
    dynamics: MicrosoftDynamicsIllustration,
    microsoft: MicrosoftDynamicsIllustration,
    databricks: DataBricksIllustration,
    postgres: PostgreSQLIllustration,
    mysql: MySQLIllustration,
    salesforce: SalesForceIllustration,
    googlereviews: GoogleReviewIllustration,
    googledrive: GoogleDriveIllustration,
    azuredatalake: AzureDataLakeStorageIllustration,
    amazons3: AmazonS3Illustration,
    sftp: SFTPIllustration,
    sharepoint: SharepointIllustration,
    sapodata: SAPODataIllustration,
    sap: SAPODataIllustration,
    netsuite: MicrosoftDynamicsIllustration,
  };

  for (const [key, image] of Object.entries(partialMatches)) {
    if (normalizedName.includes(key)) {
      return image;
    }
  }

  return fallback || DEFAULT_IMAGE;
};

/**
 * Get destination image (alias for getSourceImage for clarity)
 */
export const getDestinationImage = (
  name: string,
  fallback?: string,
): string => {
  return getSourceImage(name, fallback);
};

/**
 * Get all available images for dropdown/selection purposes
 */
export const getAllImages = () => {
  return Object.entries(IMAGE_MAP).map(([name, image]) => ({
    name: name.charAt(0).toUpperCase() + name.slice(1),
    value: name,
    image,
  }));
};

/**
 * Check if an image exists for a given name
 */
export const hasImage = (name: string): boolean => {
  if (!name) return false;
  const normalizedName = name.toLowerCase().replace(/[\s\-.]/g, "");

  // Check exact match
  if (normalizedName in IMAGE_MAP) {
    return true;
  }

  // Check if it contains any known partial match
  const knownKeys = Object.keys(IMAGE_MAP);
  return knownKeys.some((key) => normalizedName.includes(key));
};
