import type { IconType } from "react-icons";
import {
  FiCloud,
  FiGitBranch,
  FiLayers,
  FiLink2,
  FiMap,
  FiShield,
  FiTrendingUp,
  FiZap,
} from "react-icons/fi";

export type HomeFeature = {
  icon: IconType;
  title: string;
  description: string;
  bullets: string[];
  accent: "brand" | "accent" | "navy";
};

export const HOME_FEATURES: HomeFeature[] = [
  {
    icon: FiCloud,
    title: "Unified connectors",
    description:
      "One workspace for CRMs, ERPs, warehouses, databases, and file endpoints—without one-off scripts per pair.",
    bullets: [
      "Source ↔ destination pairs in minutes",
      "Credential vault per organization",
      "SAP OData via secure proxy agent",
    ],
    accent: "brand",
  },
  {
    icon: FiGitBranch,
    title: "Pipeline orchestration",
    description:
      "Design batches, chain dependencies, and watch runs in real time from the scheduler UI.",
    bullets: [
      "Visual scheduling & dependencies",
      "Retry and resume failed stages",
      "Live run status and execution logs",
    ],
    accent: "accent",
  },
  {
    icon: FiZap,
    title: "Migration engine",
    description:
      "Heavy extract/load runs on a dedicated tier so APIs stay fast while millions of rows move.",
    bullets: [
      "Incremental and full loads",
      "Schema mapping with transforms",
      "Snowflake, Databricks, lakes & files",
    ],
    accent: "brand",
  },
  {
    icon: FiShield,
    title: "Enterprise-ready security",
    description:
      "Tenant isolation, secrets outside source control, and mTLS for agents that dial out from your network.",
    bullets: [
      "Per-tenant connections & RBAC",
      "On-prem sources without inbound holes",
      "Audit-friendly activity views",
    ],
    accent: "navy",
  },
];

export type HomeStep = {
  step: string;
  icon: IconType;
  title: string;
  body: string;
  outcome: string;
};

export const HOME_STEPS: HomeStep[] = [
  {
    step: "01",
    icon: FiLink2,
    title: "Connect sources & destinations",
    body: "Add Salesforce, SAP, Postgres, or file systems on the source side. Point to Snowflake, Databricks, S3, SFTP, SharePoint, and more as targets.",
    outcome: "Secure, tenant-scoped connections",
  },
  {
    step: "02",
    icon: FiMap,
    title: "Map schemas & schedule",
    body: "Pick tables, map columns, apply transforms, then schedule batches—or wire pipelines in the scheduler.",
    outcome: "Repeatable syncs, not manual exports",
  },
  {
    step: "03",
    icon: FiTrendingUp,
    title: "Observe & iterate",
    body: "Track row counts, failures, and logs. Adjust mappings and windows without redeploying the whole stack.",
    outcome: "Continuous sync with clear ownership",
  },
];

/** Compact capability pills for the features section header area */
export const PLATFORM_PILLARS = [
  { icon: FiLayers, label: "Single control plane" },
  { icon: FiGitBranch, label: "Batch + incremental" },
  { icon: FiShield, label: "Proxy for on-prem SAP" },
] as const;
