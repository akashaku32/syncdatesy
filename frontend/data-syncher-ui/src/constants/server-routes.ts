const ServerRoutes = {
  common: {
    fetchFormSchema: ({ type, source }: { type: string; source: string }) =>
      `${source}/schema/${type}/`,
  },
  auth: {
    login: () => "/authentication/",
    registrationRequest: () => "registration-request/",
    refresh: () => "authentication/refresh/",
    profile: () => "user/profile/",

    companyDetails: ({ companyId }: { companyId: string }) =>
      `/company-details/${companyId}`,
    forgotPassword: () => "password-reset/",
    register: () => "authentication/",
    resetPassword: () => "password-reset-confirm/",
    verifyEmail: () => "verify-email/",
    resendOtp: () => "resend-otp/",
  },
  source: {
    listMasterSources: () => "source/all/",
    checkKeyPair: () => "src-check-key-pair/",
  },
  destination: {
    listMasterDestinations: () => "destinations/all/",
    selectDestination: () => "destinations/select-destination/",
    createDestination: () => "destinations/create/",
    listDestinationsByPage: ({
      page,
      size,
      searchTerm,
    }: {
      page: number;
      size: number;
      searchTerm?: string;
    }) =>
      `destinations?page=${page}&size=${size}${searchTerm ? `&name=${searchTerm}` : ""}`,
    fetchDestinationById: (id: string) => `destinations/detail/${id}`,
    // Below API to be corrected
    fetchAllUserCreatedDestinationList: () => "/destinations/?page=1&size=100",
    updateDestination: (id: string) => `destinations/update/${id}/`,
    testDestination: (id: number) => `/destinations/test/${id}/`,
    deleteDestination: (id: number) => `destinations/delete/${id}/`,
    checkKeyPair: () => "dest-check-key-pair/",
    fetchExportConfig: (destinationName: string) =>
      `destinations/${destinationName}/export-config/`,
  },
  connector: {
    listConnectorsByPage: ({
      page,
      size,
      searchTerm,
    }: {
      page: number;
      size: number;
      searchTerm?: string;
    }) =>
      `home/?page=${page}&size=${size}${searchTerm ? `&name=${searchTerm}` : ""}`,
    fetchConnectorById: (id: number) => `connection/setup/${id}/`,
    fetchConnectorConfig: ({ type, id }: { type: string; id: number }) =>
      `source-config/${type}/${id}/`,
    updateConnectorConfig: ({
      connectorId,
      type,
    }: {
      connectorId: number;
      type: string;
    }) => `source-config/${type}/${connectorId}/update/`,
    fetchConnectorUsage: (id: number) => `connection/${id}/usage/`,
    fetchConnectorSettings: (id: number) => `connection/setup/${id}/`,
    fetchConnectorTable: (id: number) => `connection/${id}/tables/`,
    fetchTableFields: (id: number, tableName: string) =>
      `connection/${id}/tables/${encodeURIComponent(tableName)}/fields/`,
    entityTransform: (id: number, tableName: string) =>
      `connection/${id}/tables/${encodeURIComponent(tableName)}/transform/`,
    entityTransformValidate: (id: number, tableName: string) =>
      `connection/${id}/tables/${encodeURIComponent(tableName)}/transform/validate/`,
    entityTransformDryRun: (id: number, tableName: string) =>
      `connection/${id}/tables/${encodeURIComponent(tableName)}/transform/dry-run/`,
    sourceScriptValidatePublish: (id: number, tableName: string) =>
      `connection/${id}/tables/${encodeURIComponent(tableName)}/source-script/validate/`,
    sourceScriptDryRun: (id: number, tableName: string) =>
      `connection/${id}/tables/${encodeURIComponent(tableName)}/source-script/dry-run/`,
    entityTransformActivate: (id: number, tableName: string, version: number) =>
      `connection/${id}/tables/${encodeURIComponent(tableName)}/transform/${version}/activate/`,
    fetchDestinationTableFields: (id: number, tableName: string) =>
      `connection/${id}/destination-tables/${encodeURIComponent(tableName)}/fields/`,
    fetchConnectorSelectedTable: (id: number) =>
      `connection/${id}/selected-tables/`,
    getTableStatus: (id: number) => `get_table_status/?connection_id=${id}`,
    fetchConnectionActivity: ({
      id,
      filterDays,
      status,
    }: {
      id: number;
      filterDays: number;
      status?: string;
    }) =>
      `connection/${id}/activity/?${
        filterDays === 1 ? `hour=${filterDays}` : `days=${filterDays}`
      }${status ? `&status=${status}` : ""}`,
    fetchConnectionActivityDetails: ({
      connectionId,
      sessionId,
    }: {
      connectionId: number;
      sessionId: number;
    }) => `connection/${connectionId}/session/${sessionId}/`,
    fetchMigrationStatus: ({
      migrationId,
      connectionId,
    }: {
      migrationId: number;
      connectionId: number;
    }) =>
      `connection/migration_status/${migrationId}/?connection_id=${connectionId}`,

    createConnector: (type: string) => `source-config/${type}/create/`,
    updateConnectionSettings: (id: number) => `connection/${id}/update/`,
    toggleStatus: (id: number) => `connection/${id}/toggle/`,
    refreshSchema: (id: number) => `connection/${id}/refresh-schema/`,
    deleteConnection: (id: number) => `connection/${id}/delete/`,
    testStatus: (id: number) => `connection/${id}/test/`,
    updateSelectedTables: (id: number) => `connection/${id}/update-selection/`,
    updateSelectedFields: (id: number, tableName: string) =>
      `connection/${id}/tables/${tableName}/table-fields/`,
    updateTableEmailGroups: (id: number, tableName: string) =>
      `connection/${id}/tables/${tableName}/email-groups/`,
    updateTableExportSettings: (id: number, tableName: string) =>
      `connection/${id}/tables/${tableName}/export-settings/`,
    updateSftpExportSettings: (id: number) =>
      `connection/${id}/sftp-export-settings/`,
    updateSchema: (id: number) => `connection/${id}/fetch-tables/`,
    updateSchemaStatus: (id: number) =>
      `connection/${id}/update-schema-status/`,
    reloadSingleTable: () => `reload-single-table/`,
    refreshDeltaTable: () => `refresh-delta-table/`,
    deleteDeltaTable: () => `delete-delta-table/`,
    fetchReverseSchema: (id: number) => `schema/${id}/`,
    fetchConnectionMappings: (id: number) => `connection/${id}/mappings/`,
    saveConnectionMappings: () => "connection/mappings/save/",
    suggestTableName: () => "s3/suggest-table-name/",
    previewdata: ({ source }: { source: string }) =>
      `${source}/preview-pattern/`,
    suggestPrimaryKeys: ({ source }: { source: string }) =>
      `${source}/suggest-primary-keys/`,
    listFiles: ({ source }: { source: string }) => `${source}/list-files/`,
    fetchLogDetails: ({
      connectionId,
      logId,
    }: {
      connectionId: number;
      logId: number;
    }) => `connection/${connectionId}/log/${logId}/`,
    batches: {
      list: (connectionId: number) => `connection/${connectionId}/batches/`,
      detail: (connectionId: number, batchId: number) =>
        `connection/${connectionId}/batches/${batchId}/`,
      assignTables: (connectionId: number, batchId: number) =>
        `connection/${connectionId}/batches/${batchId}/tables/`,
      removeTable: (connectionId: number, batchId: number, tableName: string) =>
        `connection/${connectionId}/batches/${batchId}/tables/${encodeURIComponent(tableName)}/`,
      runNow: (connectionId: number, batchId: number) =>
        `connection/${connectionId}/batches/${batchId}/run/`,
      toggle: (connectionId: number, batchId: number) =>
        `connection/${connectionId}/batches/${batchId}/toggle/`,
    },
  },
  pipeline: {
    connections: () => "pipelines/connections/",
    list: () => "pipelines/",
    detail: (pipelineId: number) => `pipelines/${pipelineId}/`,
    nodes: (pipelineId: number) => `pipelines/${pipelineId}/nodes/`,
    nodeDetail: (pipelineId: number, nodeId: number) =>
      `pipelines/${pipelineId}/nodes/${nodeId}/`,
    edges: (pipelineId: number) => `pipelines/${pipelineId}/edges/`,
    edgeDetail: (pipelineId: number, edgeId: number) =>
      `pipelines/${pipelineId}/edges/${edgeId}/`,
    run: (pipelineId: number) => `pipelines/${pipelineId}/run/`,
    validate: (pipelineId: number) => `pipelines/${pipelineId}/validate/`,
    reset: (pipelineId: number) => `pipelines/${pipelineId}/reset/`,
    runs: (pipelineId: number) => `pipelines/${pipelineId}/runs/`,
    runDetail: (pipelineId: number, runId: number) =>
      `pipelines/${pipelineId}/runs/${runId}/`,
  },
  s3: {
    listFiles: () => "s3/list-files/",
    suggestTableName: () => "s3/suggest-table-name/",
  },
  supportTickets: {
    list: () => "support-tickets/",
    create: () => "support-tickets/",
    choices: () => "support-tickets/choices/",
    detail: (id: number | string) => `support-tickets-detail/${id}/`,
    replies: (id: number | string) => `support-tickets-detail/${id}/replies/`,
    close: (id: number | string) => `support-tickets-detail/${id}/`,
  },
  user: {
    createUser: () => "users/",
    listUserRoles: () => "roles/",
    listUsersByPage: ({
      page,
      size,
      searchTerm,
    }: {
      page: number;
      size: number;
      searchTerm?: string;
    }) =>
      `users?page=${page}&size=${size}${searchTerm ? `&name=${searchTerm}` : ""}`,
    fetchUserById: (id: number) => `users/${id}`,
    updateUser: (id: number) => `users/${id}/`,
    getCurrentUserProfile: () => "user/profile/",
    updateCurrentUserProfile: () => "user/profile/",
  },
  billing: {
    listCurrentMonthBilling: ({ companyId }: { companyId: number }) =>
      `billing/${companyId}/monthly-usage/`,
    listAnnualBilling: ({ companyId }: { companyId: string }) =>
      `billing/${companyId}/annual-usage/`,
    monthlyUsage: ({ companyId }: { companyId: number }) =>
      `billing/${companyId}/monthly-usage/`,
    usage: ({
      companyId,
      billingPeriod,
    }: {
      companyId: number;
      billingPeriod?: string;
    }) =>
      `billing/usage/${companyId}/${billingPeriod ? `?billing_period=${billingPeriod}` : ""}`,
    updateConnectionsUsage: ({ companyId }: { companyId: number }) =>
      `billing/usage/update-connections/${companyId}/`,
    downloadInvoice: ({ id }: { id: number }) => `download-invoice/${id}/`,
    createPaymentIntent: ({ id }: { id: number }) => `pay/${id}/`,
    createSetupIntent: () => "billing/setup-intent/",
  },
  communicationSupport: {
    getDetails: () => "account/communication-support/",
    update: () => "account/communication-support/",
  },
  emailGroups: {
    list: () => "account/email-groups/",
    create: () => "account/email-groups/",
    update: (id: number | string) => `account/email-groups/${id}/`,
    delete: (id: number | string) => `account/email-groups/${id}/`,
  },
  emailTemplates: {
    list: () => "account/email-templates/",
    create: () => "account/email-templates/",
    update: (id: number | string) => `account/email-templates/${id}/`,
    delete: (id: number | string) => `account/email-templates/${id}/`,
  },
  account: {
    profile: () => "account/profile/",
    profileUpdate: () => "account/profile/update/",
    countries: () => "account/countries/",
  },
  proxyAgent: {
    generate: () => "proxy-agent/generate/",
    status: () => "proxy-agent/status/",
  },
} as const;

export default ServerRoutes;
