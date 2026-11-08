from django.urls import re_path
from .consumers.migration_status import MigrationStatusConsumer
from .consumers.connection_table_status import ConnectionTableStatusConsumer
from .consumers.update_schema_status_consumer import UpdateSchemaStatusConsumer
from .consumers.connection_activity_log import ConnectionActivityLogConsumer
from .consumers.company_connections_status import CompanyConnectionsStatusConsumer
from .consumers.admin_ops import AdminOpsConsumer


websocket_urlpatterns = [
    re_path(
        r"^ws/migration_status/(?P<migration_session_id>\d+)/$",
        MigrationStatusConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/connection_table_status/(?P<connection_id>\d+)/$",
        ConnectionTableStatusConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/update_schema_status/(?P<connection_id>\d+)/$",
        UpdateSchemaStatusConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/connection_activity_log/(?P<connection_id>\d+)/$",
        ConnectionActivityLogConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/company_connections_status/(?P<cmp_id>\d+)/$",
        CompanyConnectionsStatusConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/admin_ops/$",
        AdminOpsConsumer.as_asgi(),
    ),
]
