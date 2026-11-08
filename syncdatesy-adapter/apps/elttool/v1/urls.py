from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .batch_views import (
    BatchListCreateView,
    BatchDetailView,
    BatchTablesView,
    BatchTableDetailView,
    BatchRunNowView,
    BatchToggleView,
)
from .pipeline_views import (
    PipelineConnectionsView,
    PipelineDetailView,
    PipelineEdgeDetailView,
    PipelineEdgesView,
    PipelineListCreateView,
    PipelineNodeDetailView,
    PipelineNodesView,
    PipelineRunDetailView,
    PipelineRunListView,
    PipelineRunNowView,
    PipelineResetView,
    PipelineValidateView,
)
from .transform_views import (
    EntityTransformAPIView,
    EntityTransformActivateAPIView,
    EntityTransformDryRunAPIView,
    EntityTransformValidateAPIView,
)
from .source_script_views import (
    SourceScriptValidatePublishAPIView,
    SnowflakeSourceScriptDryRunAPIView,
)
from .views import (
    AuthAPIView,
    RegistrationRequestAPIView,
    VerifyOTPView, ResendOTPView,
    PasswordResetView, PasswordResetConfirmView,
    DestinationConfigListAPIView, DestinationListView,DestinationExportConfigAPIView,DestinationSchemaView,
    DestinationConfigFormView,DestinationConfigStatusView,DestinationConfigDetailView,
    SelectDestinationConfigView,DeleteDestinationConfigView,
    DestinationConfigUpdateAPIView,TestDestinationConnectionAPIView,
    SourceListView,SourceSchemaAPIView,ConnectionListAPIView,
    TestSourceConnectionAPIView,
    ConnectionTableStatusAPIView,ConnectionSchemaAPIView,ConnectionDetailAPIView,
    ConnectionUsageAPIView,UserManagementAPIView,UserProfileAPIView,
    BillingChartAPIView,BillingSummaryAPIView,BillingMonthlyUsageAPIView,BillingAnnualUsageAPIView,
    BillingPlanAPIView,CreditCardBillingAPIView,
    SelectTableAPIView,ConnectionLogsAPIView,SessionDetailAPIView,ConnectionToggleAPIView,DiscardSchemaChangesAPIView,SalesforceCallbackAPIView,GoogleDriveCallbackAPIView,ConnectionUsageChartAPIView,
    DeleteConnectionAPIView,DestinationConfigSaveAPIView,SourceConfigDetailAPIView,
    FullConnectionDetailsAPIView,SourceConnectionConfigAPIView,SourceConnectionCreateUpdateAPIView,ChangeDestinationConfigNameAPIView,
    AccountProfileAPIView, AccountProfileUpdateAPIView, CompanyDetailAPIView,ProfileDetailsAPIView,UserInfoAPIView,
    CountryListAPIView,
    FetchSourceTablesAPIView,UpdateConnectionSyncFrequencyAPIView,ListSourceTablesAPIView,UpdateSourceTableSelectionAPIView,RefreshSchemaAndMigrateAPIView,RoleListView,SelectedTablesListAPIView,ConnectionSchemaInfoAPIView,CommunicationSupportAPIView,EmailGroupListCreateAPIView,EmailGroupDetailAPIView,
    ReloadSingleTableAPIView,RefreshDeltaTableAPIView,DeleteDeltaTableAPIView,SalesforceSourceCallbackAPIView,GoogleDriveSourceCallbackAPIView,GoogleReviewsSourceCallbackAPIView,
    CheckDestinationKeyPairAPIView,CheckSourceKeyPairAPIView,TestCeleryConnectionAPIView,UpdateTimestampFieldAPIView,OAuthFlashMessageAPIView,TableMappingListAPIView,TableMappingSaveAPIView, UpdateSchemaStatusAPIView,
    TableFieldsDetailAPIView, DestinationTableFieldsDetailAPIView, MigrationStatusAPIView, UpdateTableNotificationEmailGroupsAPIView,
    UpdateTableExportSettingsAPIView, UpdateTableSftpFieldsAPIView,
    InternalMigrationStatusPushAPIView,InternalConnectionTableStatusPushAPIView,InternalUpdateSchemaStatusPushAPIView,InternalConnectionActivityLogPushAPIView,
    InternalCompanyConnectionsStatusPushAPIView, InternalSalesforceOAuthTokensAPIView,
    suggest_s3_table_name, list_s3_files, preview_s3_pattern, suggest_s3_primary_keys,
    list_sftp_files, preview_sftp_pattern, suggest_sftp_table_name, suggest_sftp_primary_keys,
    list_google_drive_files, preview_google_drive_pattern, suggest_google_drive_primary_keys, googledrive_authorize,
    list_adls_files, preview_adls_pattern, suggest_adls_primary_keys,
    TableSelectionHistoryAPIView, LogDetailAPIView,BillingUsageAPIView,DownloadInvoiceAPIView,
    CreateStripePaymentIntentAPIView, CreateStripeSetupIntentAPIView,
    )
from .ticket_support_view import SupportTicketChoicesAPIView, SupportTicketListCreateAPIView,SupportTicketDetailAPIView,TicketReplyAPIView
from .ops_views import (
    OpsOverviewAPIView,
    OpsJobsListAPIView,
    OpsJobDetailAPIView,
    OpsWorkersAPIView,
    InternalAdminOpsPushAPIView,
)
from .sap_odata_views import (
    SAPODataServiceEntitiesAPIView,
    SAPODataSaveEntitiesAPIView,
    ProxyAgentGenerateAPIView,
    ProxyAgentStatusAPIView,
)
from .proxy_agent_progress_views import InternalProxyAgentMigrationProgressAPIView
# Import RBAC views
from .rbac_views import (
    UserManagementAPIView as RBACUserManagementAPIView,
    RoleListAPIView as RBACRoleListAPIView,
)
from .email_template_views import EmailTemplateListDetailAPIView

urlpatterns = [
    path('authentication/', AuthAPIView.as_view()),
    path('registration-request/', RegistrationRequestAPIView.as_view(), name='registration-request'),
    path('authentication/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('verify-email/', VerifyOTPView.as_view(), name='verify-email'),
    path('resend-otp/', ResendOTPView.as_view(), name='resend-otp'),
    # path('signup/', SignUpAPIView.as_view(), name='signup-api'),
    # path('signin/', SignInAPIView.as_view(), name='api_signin'),
    path('password-reset/', PasswordResetView.as_view(), name='password-reset'),
    path('password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('home/', ConnectionListAPIView.as_view(), name='home-api'),
    
    # Flash messages API (clean approach for OAuth callbacks)
    path('flash-messages/', OAuthFlashMessageAPIView.as_view(), name='flash-messages'),

    path('destinations/', DestinationConfigListAPIView.as_view(),  name='destination-home-api'),
    path('destinations/all/',DestinationListView.as_view(),name="available-destinations"),
    path('destinations/<str:destination_name>/export-config/', DestinationExportConfigAPIView.as_view(), name='destination-export-config'),
    path('destinations/schema/<str:destination_name>/', DestinationSchemaView.as_view(), name='destination-schema'),
    path('destinations/config/<str:destination_name>/', DestinationConfigFormView.as_view(), name='destination-config-form'), 
    path('destinations/config/<str:destination_name>/<int:dst_config_id>/', DestinationConfigFormView.as_view(), name='destination-config-form-edit'), 
    path('destinations/status/<int:dst_config_id>/',DestinationConfigStatusView.as_view(), name='destination-config-status'),
    path('destinations/detail/<int:dst_config_id>/',DestinationConfigDetailView.as_view(), name='destination-config-detail'),

    path('destinations/select-destination/', SelectDestinationConfigView.as_view(), name='select-destination'), 
    path('destinations/delete/<int:dst_config_id>/', DeleteDestinationConfigView.as_view(), name='delete-destination'), 
    path('change-destination-name/<int:dst_config_id>/', ChangeDestinationConfigNameAPIView.as_view(), name='change_destination_name'),

    path('destinations/create/',DestinationConfigSaveAPIView.as_view(), name='destination-config-create'),
    path('destinations/update/<int:dst_config_id>/',DestinationConfigUpdateAPIView.as_view(), name='destination-config-update'),
    path('destinations/test/<int:dst_config_id>/', TestDestinationConnectionAPIView.as_view(), name='destination-test'), 
   
    

    path('source/all/',SourceListView.as_view(),name="available-sources"), 
    path('source/schema/<str:source_name>/', SourceSchemaAPIView.as_view(), name='source-schema'),
    path('source/detail/<int:src_config_id>/',SourceConfigDetailAPIView.as_view(), name='source-detail'), 

    path('source-config/<str:source_name>/', SourceConnectionConfigAPIView.as_view()),
    path('source-config/<str:source_name>/<int:connection_id>/', SourceConnectionConfigAPIView.as_view()),

    path('source-config/<str:source_name>/create/', SourceConnectionCreateUpdateAPIView.as_view()), #creation of source config and connection
    path('source-config/<str:source_name>/<int:connection_id>/update/', SourceConnectionCreateUpdateAPIView.as_view()), #updation of source config and connection
    path('source/<str:connection_name>/tables/', SelectTableAPIView.as_view(), name='select-tables'), #tested success  "message": "Table fetching initiated successfully."
    path('connection/<int:connection_id>/test/', TestSourceConnectionAPIView.as_view(), name='test-connection'), #tested success
    
    path('salesforce/callback/', SalesforceCallbackAPIView.as_view(), name='salesforce-callback'), #not tested
    path('salesforcesandbox/callback/', SalesforceCallbackAPIView.as_view(), name='salesforcesandbox-callback'), #not tested

    
    path('get_table_status/', ConnectionTableStatusAPIView.as_view(), name='get-table-status'), #tested params = ?connection_id
    path('schema/<int:connection_id>/', ConnectionSchemaAPIView.as_view(), name='schema-api'),
    path('connection/<int:connection_id>/discard/', DiscardSchemaChangesAPIView.as_view(), name='discard-schema-changes'), #not tested
    path("connection/schema-info/<int:connection_id>/", ConnectionSchemaInfoAPIView.as_view(), name="connection-schema-info"),
    path('connection/setup/<int:connection_id>/', ConnectionDetailAPIView.as_view(), name='connection-detail-api'),
    path('connection/<int:connection_id>/usage/', ConnectionUsageAPIView.as_view(), name='connection-usage-api'),
    path('connection/<int:connection_id>/activity/', ConnectionLogsAPIView.as_view(), name='connector-activity'),
    path('connection/<int:connection_id>/session/<int:session_id>/', SessionDetailAPIView.as_view(), name='session-detail'),
    path('connection/<int:connection_id>/log/<int:log_id>/', LogDetailAPIView.as_view(), name='log-detail'),
    path('connection/<int:connection_id>/toggle/', ConnectionToggleAPIView.as_view(), name='connection-toggle'),
    path('connection/<int:connection_id>/delete/',DeleteConnectionAPIView.as_view() , name='delete_connection'),
    path(
        'connection/migration_status/<int:migration_session_id>/',
        MigrationStatusAPIView.as_view(),
        name='migration_status_api'
    ),

    path('users/', UserManagementAPIView.as_view(), name='user-list-create'), #tested get , post
    path('users/<int:user_id>/', UserManagementAPIView.as_view(), name='user-detail-update'), #tested put method
    path('user/profile/', UserProfileAPIView.as_view(), name='user-profile'), #tested 
    path('email-templates/', EmailTemplateListDetailAPIView.as_view(), name='email-template-list-create'),
    path('email-templates/<int:template_id>/', EmailTemplateListDetailAPIView.as_view(), name='email-template-detail'),
    path('account/email-templates/', EmailTemplateListDetailAPIView.as_view(), name='account-email-template-list-create'),
    path('account/email-templates/<int:template_id>/', EmailTemplateListDetailAPIView.as_view(), name='account-email-template-detail'),

    # ===========================================================================================
    # RBAC API ENDPOINTS (Role-Based Access Control)
    # ===========================================================================================
    path('rbac/users/', RBACUserManagementAPIView.as_view(), name='rbac-user-list-create'),
    path('rbac/users/<int:user_id>/', RBACUserManagementAPIView.as_view(), name='rbac-user-update'),
    path('rbac/roles/', RBACRoleListAPIView.as_view(), name='rbac-roles'),
 

    path('billing/chart/<int:cmp_id>/', BillingChartAPIView.as_view(), name='billing-chart'),
    path('billing/summary/<int:cmp_id>/', BillingSummaryAPIView.as_view(), name='billing-summary'),
    path(
        'billing/usage/<int:cmp_id>/',
        BillingUsageAPIView.as_view(),
        name='billing_usage_api'
    ),
    path(
        "download-invoice/<int:id>/",
        DownloadInvoiceAPIView.as_view(),
        name="download_invoice"
    ),
    path("pay/<int:invoice_id>/", CreateStripePaymentIntentAPIView.as_view()),
    path("billing/setup-intent/", CreateStripeSetupIntentAPIView.as_view(), name="billing-setup-intent"),
    path('billing/<int:cmp_id>/monthly-usage/', BillingMonthlyUsageAPIView.as_view(),name="billing_monthly_usage"),
    path('billing/<int:cmp_id>/annual-usage/', BillingAnnualUsageAPIView.as_view(),name="billing_annual_usage"),
    path('billing/usage/update-connections/<int:cmp_id>/', ConnectionUsageChartAPIView.as_view(), name='update_connections'),
    path('billing/plan/', BillingPlanAPIView.as_view(), name='billing-plan'),
    path('billing/plan/<int:cmp_id>/', BillingPlanAPIView.as_view(), name='billing-plan-detail'),
    path('billing/payment/', CreditCardBillingAPIView.as_view(), name='credit-card-billing'),
    path('billing/payment/<int:cmp_id>/', CreditCardBillingAPIView.as_view(), name='credit-card-billing-detail'),

    path('support-tickets/', SupportTicketListCreateAPIView.as_view(), name='support_ticket_list_create'),
    path('support-tickets/choices/', SupportTicketChoicesAPIView.as_view(), name='support_ticket_choices'),
    path('support-tickets-detail/<int:ticket_id>/', SupportTicketDetailAPIView.as_view(), name='support_ticket_detail'),
    path('support-tickets-detail/<int:ticket_id>/replies/', TicketReplyAPIView.as_view(), name='support_ticket_replies'),
    path('full-connections/', FullConnectionDetailsAPIView.as_view(), name='full-connection-list'),
    path('full-connections/<int:connection_id>/', FullConnectionDetailsAPIView.as_view(), name='full-connection-detail'),

    

    
    path('company-details/<int:pk>/',CompanyDetailAPIView.as_view(),name="company-details"),
    path('account/profile/', AccountProfileAPIView.as_view(), name='account-profile'),
    path('account/profile/update/', AccountProfileUpdateAPIView.as_view(), name='account-profile-update'),
    path('account/countries/', CountryListAPIView.as_view(), name='account-countries'),
    path('profile-details/',ProfileDetailsAPIView.as_view(),name="profile-details"),
    path('user/info/', UserInfoAPIView.as_view(), name='user-info'),
  
    
    path('connection/<int:connection_id>/fetch-tables/', FetchSourceTablesAPIView.as_view()),
    path('connection/<int:connection_id>/tables/', ListSourceTablesAPIView.as_view()),
    path('connection/<int:connection_id>/tables/<str:table_name>/fields/', TableFieldsDetailAPIView.as_view(), name='table-fields-detail'),
    path('connection/<int:connection_id>/destination-tables/<str:table_name>/fields/', DestinationTableFieldsDetailAPIView.as_view(), name='destination-table-fields-detail'),
    path('connection/<int:connection_id>/tables/<str:table_name>/table-fields/', UpdateTableSftpFieldsAPIView.as_view(), name='table-sftp-fields'),
    path('connection/<int:connection_id>/tables/<str:table_name>/email-groups/', UpdateTableNotificationEmailGroupsAPIView.as_view(), name='table-email-groups'),
    path('connection/<int:connection_id>/tables/<str:table_name>/table-fields/', TableFieldsDetailAPIView.as_view(), name='table-fields-detail-alias'),
    path('connection/<int:connection_id>/tables/<str:table_name>/export-settings/', UpdateTableExportSettingsAPIView.as_view(), name='table-export-settings'),
    path('connection/<int:connection_id>/tables/<str:table_name>/transform/', EntityTransformAPIView.as_view(), name='entity-transform'),
    path('connection/<int:connection_id>/tables/<str:table_name>/transform/validate/', EntityTransformValidateAPIView.as_view(), name='entity-transform-validate'),
    path('connection/<int:connection_id>/tables/<str:table_name>/transform/dry-run/', EntityTransformDryRunAPIView.as_view(), name='entity-transform-dry-run'),
    path('connection/<int:connection_id>/tables/<str:table_name>/source-script/validate/', SourceScriptValidatePublishAPIView.as_view(), name='source-script-validate-publish'),
    path('connection/<int:connection_id>/tables/<str:table_name>/source-script/dry-run/', SnowflakeSourceScriptDryRunAPIView.as_view(), name='snowflake-source-script-dry-run'),
    path('connection/<int:connection_id>/tables/<str:table_name>/transform/<int:version>/activate/', EntityTransformActivateAPIView.as_view(), name='entity-transform-activate'),
    path('connection/<int:connection_id>/selected-tables/', SelectedTablesListAPIView.as_view(), name='selected-tables-list'),
    path('connection/<int:connection_id>/update-selection/', UpdateSourceTableSelectionAPIView.as_view()),
    path('connection/<int:connection_id>/table-selection-history/', TableSelectionHistoryAPIView.as_view(), name='table-selection-history'),
    path('connection/<int:connection_id>/refresh-schema/', RefreshSchemaAndMigrateAPIView.as_view()),
    path('connection/<int:connection_id>/update-sync-frequency/', UpdateConnectionSyncFrequencyAPIView.as_view()),
    path('roles/',RoleListView.as_view(),name="roles"),
    path('account/communication-support/', CommunicationSupportAPIView.as_view(), name='communication-support'),
    path('account/email-groups/', EmailGroupListCreateAPIView.as_view(), name='email-group-list-create'),
    path('account/email-groups/<int:group_id>/', EmailGroupDetailAPIView.as_view(), name='email-group-detail'),
    
    # Single table operations
    path('reload-single-table/', ReloadSingleTableAPIView.as_view(), name='reload-single-table'),
    path('refresh-delta-table/', RefreshDeltaTableAPIView.as_view(), name='refresh-delta-table'),
    path('delete-delta-table/', DeleteDeltaTableAPIView.as_view(), name='delete-delta-table'),
    
    # Salesforce source OAuth callbacks
    path('source/salesforce/callback/', SalesforceSourceCallbackAPIView.as_view(), name='source-salesforce-callback'),
    path('source/salesforcesandbox/callback/', SalesforceSourceCallbackAPIView.as_view(), name='source-salesforcesandbox-callback'),

    #google drive source OAuth callback
    path('source/googledrive/authorize/', googledrive_authorize, name='googledrive-authorize'),
    path('source/googledrive/callback', GoogleDriveSourceCallbackAPIView.as_view(), name='source-googledrive-callback'),
    path('source/googledrive/callback/', GoogleDriveSourceCallbackAPIView.as_view(), name='source-googledrive-callback-slash'),
    
    # Google Drive destination OAuth callback
    path('google-drive/callback/', GoogleDriveCallbackAPIView.as_view(), name='google-drive-callback'),
    
    # Google Drive destination OAuth callback
    path('google-drive/callback/', GoogleDriveCallbackAPIView.as_view(), name='google-drive-callback'),
    
    # Google Reviews source OAuth callback
    path('source/googlereviews/callback/', GoogleReviewsSourceCallbackAPIView.as_view(), name='source-googlereviews-callback'),
    
    # Key pair check endpoints
    path('dest-check-key-pair/', CheckDestinationKeyPairAPIView.as_view(), name='dest-check-key-pair'),
    path('src-check-key-pair/', CheckSourceKeyPairAPIView.as_view(), name='src-check-key-pair'),
    
    # Celery connectivity test
    path('test-celery-connection/', TestCeleryConnectionAPIView.as_view(), name='test-celery-connection'),
    
    # Update timestamp field for delta sync
    path('connection/update-timestamp/', UpdateTimestampFieldAPIView.as_view(), name='update-timestamp-field'),

    path("connection/<int:connection_id>/mappings/", TableMappingListAPIView.as_view(), name="list-mappings"),
    path("connection/mappings/save/", TableMappingSaveAPIView.as_view(), name="save-mappings"),
    path('connection/<int:connection_id>/update-schema-status/', UpdateSchemaStatusAPIView.as_view(), name='update-schema-status'),

    # S3 helper endpoints for React mapping UI
    path('s3/suggest-table-name/', suggest_s3_table_name, name='s3-suggest-table-name'),
    path('s3/list-files/', list_s3_files, name='s3-list-files'),
    path('s3/preview-pattern/', preview_s3_pattern, name='s3-preview-pattern'),
    path('s3/suggest-primary-keys/', suggest_s3_primary_keys, name='s3-suggest-primary-keys'),
    # SFTP helper endpoints for React mapping UI
    path('sftp/list-files/', list_sftp_files, name='sftp-list-files'),
    path('sftp/preview-pattern/', preview_sftp_pattern, name='sftp-preview-pattern'),
    path('sftp/suggest-table-name/', suggest_sftp_table_name, name='sftp-suggest-table-name'),
    path('sftp/suggest-primary-keys/', suggest_sftp_primary_keys, name='sftp-suggest-primary-keys'),
    # Google Drive helper endpoints for React mapping UI
    path('googledrive/list-files/', list_google_drive_files, name='googledrive-list-files'),
    path('googledrive/preview-pattern/', preview_google_drive_pattern, name='googledrive-preview-pattern'),
    path('googledrive/suggest-primary-keys/', suggest_google_drive_primary_keys, name='googledrive-suggest-primary-keys'),
    # ADLS helper endpoints for React mapping UI
    path('azuredatalakestorage/list-files/', list_adls_files, name='azuredatalakestorage-list-files'),
    path('azuredatalakestorage/preview-pattern/', preview_adls_pattern, name='azuredatalakestorage-preview-pattern'),
    path('azuredatalakestorage/suggest-primary-keys/', suggest_adls_primary_keys, name='azuredatalakestorage-suggest-primary-keys'),
    path('internal/migration-status-push/<int:migration_session_id>/', InternalMigrationStatusPushAPIView.as_view(), name='internal-migration-status-push'),

    path('internal/connection-table-status-push/<int:connection_id>/', InternalConnectionTableStatusPushAPIView.as_view(), name='internal-connection-table-status-push'),
    
    path('internal/connection-activity-log-push/<int:connection_id>/', InternalConnectionActivityLogPushAPIView.as_view(), name='internal-connection-activity-log-push'),
    
    path('internal/update-schema-status-push/<int:connection_id>/', InternalUpdateSchemaStatusPushAPIView.as_view(), name='internal-update-schema-status-push'),

    path('internal/company-connections-status-push/<int:cmp_id>/', InternalCompanyConnectionsStatusPushAPIView.as_view(), name='internal-company-connections-status-push'),

    path(
        'internal/connection/<int:connection_id>/salesforce-oauth-tokens/',
        InternalSalesforceOAuthTokensAPIView.as_view(),
        name='internal-salesforce-oauth-tokens',
    ),

    # ===========================================================================================
    # BATCH SCHEDULING API (Dashboard Scheduling feature)
    # ===========================================================================================
    path('connection/<int:connection_id>/batches/', BatchListCreateView.as_view(), name='batch-list-create'),
    path('connection/<int:connection_id>/batches/<int:batch_id>/', BatchDetailView.as_view(), name='batch-detail'),
    path('connection/<int:connection_id>/batches/<int:batch_id>/tables/', BatchTablesView.as_view(), name='batch-tables'),
    path('connection/<int:connection_id>/batches/<int:batch_id>/tables/<str:table_name>/', BatchTableDetailView.as_view(), name='batch-table-detail'),
    path('connection/<int:connection_id>/batches/<int:batch_id>/run/', BatchRunNowView.as_view(), name='batch-run-now'),
    path('connection/<int:connection_id>/batches/<int:batch_id>/toggle/', BatchToggleView.as_view(), name='batch-toggle'),

    # ===========================================================================================
    # PIPELINE SCHEDULING API (Dashboard Scheduling Canvas)
    # ===========================================================================================
    path('pipelines/connections/', PipelineConnectionsView.as_view(), name='pipeline-connections'),
    path('pipelines/', PipelineListCreateView.as_view(), name='pipeline-list-create'),
    path('pipelines/<int:pipeline_id>/', PipelineDetailView.as_view(), name='pipeline-detail'),
    path('pipelines/<int:pipeline_id>/nodes/', PipelineNodesView.as_view(), name='pipeline-nodes'),
    path('pipelines/<int:pipeline_id>/nodes/<int:node_id>/', PipelineNodeDetailView.as_view(), name='pipeline-node-detail'),
    path('pipelines/<int:pipeline_id>/edges/', PipelineEdgesView.as_view(), name='pipeline-edges'),
    path('pipelines/<int:pipeline_id>/edges/<int:edge_id>/', PipelineEdgeDetailView.as_view(), name='pipeline-edge-detail'),
    path('pipelines/<int:pipeline_id>/validate/', PipelineValidateView.as_view(), name='pipeline-validate'),
    path('pipelines/<int:pipeline_id>/reset/', PipelineResetView.as_view(), name='pipeline-reset'),
    path('pipelines/<int:pipeline_id>/run/', PipelineRunNowView.as_view(), name='pipeline-run'),
    path('pipelines/<int:pipeline_id>/runs/', PipelineRunListView.as_view(), name='pipeline-run-list'),
    path('pipelines/<int:pipeline_id>/runs/<int:run_id>/', PipelineRunDetailView.as_view(), name='pipeline-run-detail'),

    path('admin/ops/overview/', OpsOverviewAPIView.as_view(), name='admin-ops-overview'),
    path('admin/ops/jobs/', OpsJobsListAPIView.as_view(), name='admin-ops-jobs'),
    path('admin/ops/jobs/<path:job_name>/', OpsJobDetailAPIView.as_view(), name='admin-ops-job-detail'),
    path('admin/ops/workers/', OpsWorkersAPIView.as_view(), name='admin-ops-workers'),
    path('internal/admin-ops-push/', InternalAdminOpsPushAPIView.as_view(), name='internal-admin-ops-push'),
    path(
        'internal/proxy-agent/migration-progress/',
        InternalProxyAgentMigrationProgressAPIView.as_view(),
        name='internal-proxy-agent-migration-progress',
    ),

    # SAP OData Service Discovery Endpoints
    path('sap-odata/service-entities/', SAPODataServiceEntitiesAPIView.as_view(), name='sap-odata-service-entities'),
    path('sap-odata/save-entities/', SAPODataSaveEntitiesAPIView.as_view(), name='sap-odata-save-entities'),

    # Proxy Agent Config Generation & Status
    path('proxy-agent/generate/', ProxyAgentGenerateAPIView.as_view(), name='proxy-agent-generate'),
    path('proxy-agent/status/', ProxyAgentStatusAPIView.as_view(), name='proxy-agent-status'),
]

