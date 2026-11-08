from django.db import transaction
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
import logging
from .models import SourceTableSelection, Log, ScheduledJob, MigrationRecord

logger = logging.getLogger(__name__)

# Store old values before save
@receiver(pre_save, sender=SourceTableSelection)
def capture_old_status(sender, instance, **kwargs):
    """
    Capture old status before saving to detect changes
    """
    if instance.pk:  # Only for existing instances
        try:
            old_instance = SourceTableSelection.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
            instance._old_connection_id = old_instance.connection.connection_id if old_instance.connection else None
        except SourceTableSelection.DoesNotExist:
            pass  # New instance, no old values to capture

# Trigger WebSocket when status changes
@receiver(post_save, sender=SourceTableSelection)
def send_table_status_websocket_update(sender, instance, **kwargs):
    """
    Send WebSocket update when SourceTableSelection status changes
    """
    # Check if status actually changed
    old_status = getattr(instance, '_old_status', None)
    new_status = instance.status
    
    # Only trigger if status changed and we have a connection
    if old_status != new_status and instance.connection:
        connection_id = instance.connection.connection_id
        
        logger.info(
            f"🔔 WebSocket update sent for connection {connection_id} | "
            f"table='{instance.table}' | status: {old_status} → {new_status}"
        )
        
        try:
            # Import here to avoid circular imports
            from apps.elttool.websocket.ws_utils import trigger_connection_ws_status_update
            trigger_connection_ws_status_update(connection_id)
        except Exception as e:
            logger.error(f"❌ Failed to trigger WebSocket update: {e}")


# Note: Log activity WebSocket trigger has been moved to Log.save() in models.py
# to handle both creation and updates consistently.


@receiver(post_save, sender=ScheduledJob)
def send_next_sync_websocket_update(sender, instance, **kwargs):
    """
    Send WebSocket update when ScheduledJob changes so next_sync_time can refresh live.
    """
    if not instance.connection_id:
        return

    connection_id = instance.connection_id

    def trigger_ws():
        try:
            from apps.elttool.websocket.ws_utils import trigger_connection_ws_status_update
            logger.info(
                "🔔 ScheduledJob changed, triggering WS update for connection %s",
                connection_id
            )
            trigger_connection_ws_status_update(connection_id)
        except Exception as e:
            logger.error(f"❌ Failed to trigger WS update for ScheduledJob: {e}")

    transaction.on_commit(trigger_ws)


@receiver(pre_save, sender=MigrationRecord)
def capture_migration_record_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = MigrationRecord.objects.get(pk=instance.pk)
            instance._old_status = old.status
        except MigrationRecord.DoesNotExist:
            pass


@receiver(post_save, sender=MigrationRecord)
def push_admin_ops_on_migration_status_change(sender, instance, **kwargs):
    old_status = getattr(instance, "_old_status", None)
    if old_status == instance.status:
        return

    def trigger_ops_push():
        try:
            from apps.elttool.services.ops_monitoring_service import build_ops_snapshot
            from apps.elttool.websocket.ws_utils import push_admin_ops_snapshot

            push_admin_ops_snapshot(build_ops_snapshot())
            logger.info(
                "Admin ops snapshot pushed | job=%s | status: %s → %s",
                instance.job_name,
                old_status,
                instance.status,
            )
        except Exception as exc:
            logger.error("Failed to push admin ops snapshot: %s", exc)

    transaction.on_commit(trigger_ops_push)
