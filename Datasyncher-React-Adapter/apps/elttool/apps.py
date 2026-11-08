import os
import logging
from django.apps import AppConfig
from django.conf import settings

# Initialize logging for debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger(__name__)

# Global scheduler variable (only initialized if running in scheduler container)
scheduler = None

# AppConfig Class
class ElttoolConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.elttool'

    def ready(self):
        global scheduler
        
        print("=" * 80)
        print("[elttool] ELTTOOL CONFIG READY() CALLED")
        print("=" * 80)
        print(f"   Process PID: {os.getpid()}")
        
        # Import and register signals
        # This ensures signals are connected when Django starts
        try:
            import apps.elttool.signals  # noqa: F401
            logger.info("WebSocket table status signals registered")
        except Exception as e:
            logger.error("Failed to register signals: %s", e, exc_info=True)

        # Check environment variable to determine if this is a scheduler container
        is_scheduler_container = os.environ.get('RUN_SCHEDULER', 'false').lower() == 'true'
        
        if is_scheduler_container:
            # OPTIONAL: If you ever want to run the scheduler via this apps.py in a dedicated container
            # instead of using the standalone scheduler_service.py, you would initialize it here.
            # Currently, we defer to scheduler_service.py, but we log strictly for clarity.
            logger.info(
                "RUN_SCHEDULER=true detected, but scheduler is managed by scheduler_service.py."
            )
            logger.info("    To avoid conflicts, we are NOT starting an internal scheduler here.")
            
            # If you WANTED to run it here, you would import BackgroundScheduler etc. and start it.
            # For now, we keep it disabled to prevent duplicate executions.
        else:
            # This is a web worker or other container
            logger.info(
                "Running as web worker - scheduler disabled (using external scheduler service)."
            )
            print(
                "   Internal scheduler disabled to prevent duplicate job executions."
            )
        
        print("=" * 80)
