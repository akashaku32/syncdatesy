const ClientRoutes = {
  // Website
  HOME: "/",

  // Auth
  AUTH: "/auth",
  LOGIN: "login",
  REGISTER: "register",
  FORGOT_PASSWORD: "forgot-password",
  RESET_PASSWORD: "reset-password",
  CHANGE_INITIAL_PASSWORD: "change-initial-password",
  VERIFY_EMAIL: "verify-email",

  // Dashboard
  DASHBOARD: "/dashboard",
  CONNECTORS: {
    ROOT: "connectors",
    ADD: "add",
    EDIT: "edit",
    OVERVIEW: "overview",
    SCHEMA: "schema",
    REVERSE_SCHEMA: "reverse-schema",
    USAGE: "usage",
    SETTINGS: "settings",
    EDIT_CONFIGURATION: "edit-configuration",
  },
  DESTINATION: {
    ROOT: "destinations",
    ADD: "add",
    EDIT: "edit",
    CONFIGURE: "configure",
  },
  SCHEDULING: "scheduling",
  PLANS: "plans",
  SUPPORT: "support",
  USER_SETTINGS: {
    ROOT: "user-settings",
    USERS: "users",
    USER_ADD: "add",
    USER_EDIT: "edit",
    PROFILE: "profile",
  },
  ACCOUNT_SETTINGS: {
    ROOT: "account",
    BILLING: "billing",
    EMAIL: "communication-support",
    PROFILE: "profile",
    AGENT_DOWNLOADS: "agent-downloads",
  },
} as const;
export default ClientRoutes;
