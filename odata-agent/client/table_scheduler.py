
# table_scheduler.py

import asyncio
import logging
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from odata_functions import load_delta,load_initial
from to_snowflake import sap_odata_to_snowflake_connector
from datetime import datetime
from time import perf_counter
from to_snowflake import update_migration_status_log


class TableScheduler:
    def __init__(self, max_workers=4):
        self.max_workers = max_workers


        # Scheduler owns the shared process pool.
        self.process_pool = ProcessPoolExecutor(
            max_workers=max_workers
        )

        # Stores messages and their remaining tables.
        # {
        #     message_id: {
        #         "response": res,
        #         "tables": deque([...]),
        #     }
        # }
        self.pending_message = {}

        # Message IDs in round-robin order.
        self.message_order = deque()

        # Futures for currently running table jobs.
        self.running_jobs = set()

        self.next_message_id = 1
        self.wakeup = asyncio.Event()
        self.scheduler_task = None
        self.closed = False
        self.initial_workers = max_workers // 2
        self.reservation_window_seconds=2.0
        self.reservation_active = False

    async def start(self):
        """Start the scheduling loop once."""
        if self.scheduler_task is None:
            self.scheduler_task = asyncio.create_task(
                self._schedule()
            )

    async def add_message(self, message):
        """
        Receive a message from the agent.

        Expected structure:
            {
                "response": res,
                "tables": res.sources,
            }
        """
        if self.closed:
            raise RuntimeError("TableScheduler is closed")

        response = message["response"]
        tables = list(message.get("tables", []))

        if not tables:
            print("Message contains no tables")
            return

        await self.start()

        # First message in an idle period.
        if (
            not self.pending_message
            and not self.running_jobs
            and not self.reservation_active
        ):
            self._start_reservation_window()

        # Another message arrived during the reservation window.
        elif self.reservation_active:
            self._release_reservation()

        message_id = self.next_message_id
        self.next_message_id += 1

        self.pending_message[message_id] = {
            "response": response,
            "tables": deque(tables),
            "running": 0,
            # ------------------------------------------------------------------------------------
            "total_tables": len(tables),
            "completed_tables": 0,
            "results": [],
            "started_perf": perf_counter(),
            #-------------------------------------------------------------------------------------
        }

        self.message_order.append(message_id)

        print(
            "Message %s queued with %s tables",
            message_id,
            len(tables),
        )

        self.wakeup.set()

    def _start_reservation_window(self):

        self.reservation_active = True
        self.reservation_task = asyncio.create_task(
            self._release_reservation_after_timeout()
        )


    async def _release_reservation_after_timeout(self):
        try:
            await asyncio.sleep(
                self.reservation_window_seconds
            )
            self.reservation_active = False

        except asyncio.CancelledError:
            pass


    def _release_reservation(self):

        if not self.reservation_active:
            return

        self.reservation_active = False

        current_task = asyncio.current_task()

        if (
            self.reservation_task is not None
            and self.reservation_task is not current_task
            and not self.reservation_task.done()
        ):
            self.reservation_task.cancel()

        self.wakeup.set()

    def _current_dispatch_limit(self):

        if self.reservation_active:
            return self.initial_workers

        return self.max_workers

    async def _schedule(self):
        """Dispatch one table at a time using round-robin."""
        loop = asyncio.get_running_loop()

        while True:
            # Submit jobs while worker capacity is available.
            dispatch_limit = self._current_dispatch_limit()
            while (
                self.message_order
                and len(self.running_jobs) < dispatch_limit
            ):
                message_id = self.message_order.popleft()
                entry = self.pending_message.get(message_id)
                if entry is None:
                    continue

                # Pick one table from this message.
                table = entry["tables"].popleft()
                response = entry["response"]

                entry["running"] += 1

                # Give other messages their turn before this one
                # receives another dispatch.
                if entry["tables"]:
                    self.message_order.append(message_id)

                print(
                    "Dispatching message %s, table %s",
                    message_id,
                    getattr(table, "entity_name", "unknown"),
                )

                # Submit one table to the shared process pool.
                future = loop.run_in_executor(
                    self.process_pool,
                    execute_table,
                    response,
                    table,
                )

                self.running_jobs.add(future)
                future.add_done_callback(
                    lambda completed, mid=message_id:
                        self._job_finished(completed, mid)
                )
                self._cleanup_message(message_id)

            # Exit after shutdown when all jobs have drained.
            if (
                self.closed
                and not self.message_order
                and not self.running_jobs
            ):
                return

            self.wakeup.clear()

            if not self.running_jobs and not self.message_order:
                await self.wakeup.wait()
                continue

            # Wait for a worker to finish or for a new message.
            if self.running_jobs:
                wake_task = asyncio.create_task(
                    self.wakeup.wait()
                )

                done, _ = await asyncio.wait(
                    self.running_jobs | {wake_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if wake_task not in done:
                    wake_task.cancel()
                    await asyncio.gather(
                        wake_task,
                        return_exceptions=True,
                    )

    def _job_finished(self, future, message_id):
        """Handle a completed or failed table job."""
        self.running_jobs.discard(future)

        entry = self.pending_message.get(message_id)

        if entry is not None:
            entry["running"] -= 1

        try:
            result = future.result()
            print(
                "Message %s table completed: %s",
                message_id,
                result,
            )
        except Exception as exc:
            print(
                "Table job failed for message %s",
                message_id,
            )

            # This is for exceptions escaping the normal
            # structured-result contract.
            result = {
                "table_name": "UNKNOWN",
                "status": "failed",
                "error": str(exc),
                "rows_loaded": 0,
            }
        # ---------------------------------------------------------------------------------
        # -----------------------------------------
        # Collect table result
        # -----------------------------------------

        entry["results"].append(result)

        # -----------------------------------------
        # Increment completed table count
        # -----------------------------------------

        entry["completed_tables"] += 1

        # -----------------------------------------
        # Check whether all tables have completed
        # -----------------------------------------

        if (
            entry["completed_tables"]
            == entry["total_tables"]
        ):

            self._cleanup_message(message_id)
        # ---------------------------------------------------------------------------------
        

        # Immediately allow the scheduler to dispatch
        # another table.
        self.wakeup.set()

    def _cleanup_message(self, message_id):
        """update the migration status and Remove a message only when all its work has finished."""
        entry = self.pending_message.get(message_id)

        if entry is None:
            return
        
        # ---------------------------------------------------------------------------------

        # Make sure all tables have completed.
        if entry["completed_tables"] != entry["total_tables"]:
            return

        results = entry["results"]
        response = entry["response"]


        successful = [
            r
            for r in results
            if str(r.get("status", "")).lower() in ("success", "completed")
        ]
        failed = [
            r
            for r in results
            if str(r.get("status", "")).lower() not in ("success", "completed")
        ]
        total_rows = sum(int(r.get("rows_loaded") or 0) for r in results)
        if failed:
            summary_error = "; ".join(
                f"{r.get('table_name')}: {r.get('error')}"
                for r in failed
                if r.get("table_name")
            )
        else:
            summary_error = None

        total_time = perf_counter() - entry["started_perf"]
        
        completion_message = (
            summary_error
            if failed
            else f"Migration completed successfully in {total_time:.2f} seconds. Total rows: {total_rows}."
        )
        job_name = response.details_for_sts_update["job_name"]
        
        if job_name:
            update_migration_status_log({
            "job_name":job_name,
            "message":completion_message,
            "status":"F" if failed else "S",
            "session":response.details_for_sts_update["session"],
            "log_id":response.details_for_sts_update["log_id"],
            "celery_child_mode":False,}
        )
        # ---------------------------------------------------------------------------------

        if not entry["tables"] and entry["running"] == 0:
            del self.pending_message[message_id]

            print(
                "Message %s fully processed",
                message_id,
            )

    async def shutdown(self):
        """Stop accepting jobs and gracefully close the pool."""
        if self.closed:
            return

        self.closed = True
        self.wakeup.set()

        if self.scheduler_task is not None:
            await self.scheduler_task

        self.process_pool.shutdown(wait=True)

        print("TableScheduler shut down")


def execute_table(response, table_data):
    """
    Process exactly one table.

    Replace this example dispatcher with your actual single-table
    ADLS initial, ADLS delta, and Snowflake processing functions.
    This function must remain at module level for process pickling.
    """


    if response.load_delta == True and response.destination_type == "adls":
        """ delta load can be of two types 

            if load_method == "delete and load" then use the load_initial() because its already deletes the old data and loads freshly
            
            and for fetching deltas using delta token use load_delta()

        """

        if table_data.load_method == "delete and load":
            return load_delta(
                response.src_config,
                response.destination_type,
                response.destination_config,
                table_data,
                response.details_for_sts_update           
            )
        else:
            return load_initial(
                response.src_config,
                response.destination_type,
                response.destination_config,
                table_data,
                response.details_for_sts_update
            )
    elif response.sources and response.destination_type == "adls":
            return load_initial(
                response.src_config,
                response.destination_type,
                response.destination_config,
                table_data,
                response.details_for_sts_update
            )

    elif response.destination_type == "snowflake":
        return sap_odata_to_snowflake_connector(response,table_data)

    raise ValueError(
        f"Unsupported destination type: {response.destination}"
    )






import asyncio
from collections import deque
from concurrent.futures import ProcessPoolExecutor

from odata_functions import load_delta, load_initial
from to_snowflake import sap_odata_to_snowflake_connector


# class TableScheduler:

#     def __init__(
#         self,
#         max_workers=4,
#         initial_workers=None,
#         reservation_window_seconds=2.0,
#     ):
#         self.max_workers = max(1, int(max_workers))
#         self.reservation_window_seconds = max(
#             0.0, float(reservation_window_seconds)
#         )

#         # Initially allow only half of the process pool to be occupied.
#         if initial_workers is None:
#             initial_workers = max(1, self.max_workers // 2)

#         self.initial_workers = min(
#             self.max_workers,
#             max(1, int(initial_workers))
#         )

#         # One shared process pool for all messages.
#         self.process_pool = ProcessPoolExecutor(
#             max_workers=self.max_workers
#         )

#         # Stores messages and their remaining tables.
#         self.pending_message = {}

#         # Message IDs in round-robin order.
#         self.message_order = deque()

#         # Currently running futures.
#         self.running_jobs = set()

#         self.next_message_id = 1

#         self.wakeup = asyncio.Event()
#         self.scheduler_task = None
#         self.reservation_task = None

#         self.reservation_active = False
#         self.closed = False

#     # --------------------------------------------------
#     # START SCHEDULER
#     # --------------------------------------------------

#     async def start(self):

#         if self.scheduler_task is None:
#             self.scheduler_task = asyncio.create_task(
#                 self._schedule()
#             )

#     # --------------------------------------------------
#     # ADD MESSAGE
#     # --------------------------------------------------

#     async def add_message(self, message):

#         if self.closed:
#             raise RuntimeError("TableScheduler is closed")

#         response = message["response"]
#         tables = list(message.get("tables", []))

#         if not tables:
#             print("Message contains no tables")
#             return

#         await self.start()

#         # First message in an idle period.
#         if (
#             not self.pending_message
#             and not self.running_jobs
#             and not self.reservation_active
#         ):
#             self._start_reservation_window()

#         # Another message arrived during the reservation window.
#         elif self.reservation_active:

#             self._release_reservation(
#                 "another message arrived"
#             )

#         message_id = self.next_message_id
#         self.next_message_id += 1

#         self.pending_message[message_id] = {
#             "response": response,
#             "tables": deque(tables),
#             "running": 0,
#         }

#         self.message_order.append(message_id)

#         print(
#             f"Message {message_id} queued "
#             f"with {len(tables)} tables"
#         )

#         self.wakeup.set()

#     # --------------------------------------------------
#     # START RESERVATION WINDOW
#     # --------------------------------------------------

#     def _start_reservation_window(self):

#         self.reservation_active = (
#             self.initial_workers < self.max_workers
#         )

#         if (
#             not self.reservation_active
#             or self.reservation_window_seconds == 0
#         ):
#             self.reservation_active = False
#             return

#         print(
#             f"Reservation active for "
#             f"{self.reservation_window_seconds:.2f} seconds. "
#             f"Initial dispatch limit: "
#             f"{self.initial_workers}/{self.max_workers}"
#         )

#         self.reservation_task = asyncio.create_task(
#             self._release_reservation_after_timeout()
#         )

#     # --------------------------------------------------
#     # RESERVATION TIMER
#     # --------------------------------------------------

#     async def _release_reservation_after_timeout(self):

#         try:
#             await asyncio.sleep(
#                 self.reservation_window_seconds
#             )

#             self._release_reservation(
#                 "reservation window expired"
#             )

#         except asyncio.CancelledError:
#             pass

#     # --------------------------------------------------
#     # RELEASE RESERVED WORKERS
#     # --------------------------------------------------

#     def _release_reservation(self, reason):

#         if not self.reservation_active:
#             return

#         self.reservation_active = False

#         print(
#             f"Worker reservation released: {reason}"
#         )

#         current_task = asyncio.current_task()

#         if (
#             self.reservation_task is not None
#             and self.reservation_task is not current_task
#             and not self.reservation_task.done()
#         ):
#             self.reservation_task.cancel()

#         self.wakeup.set()

#     # --------------------------------------------------
#     # CURRENT DISPATCH LIMIT
#     # --------------------------------------------------

#     def _current_dispatch_limit(self):

#         if self.reservation_active:
#             return self.initial_workers

#         return self.max_workers

#     # --------------------------------------------------
#     # MAIN SCHEDULER
#     # --------------------------------------------------

#     async def _schedule(self):

#         loop = asyncio.get_running_loop()

#         while True:

#             dispatch_limit = self._current_dispatch_limit()

#             # Dispatch while worker capacity is available.
#             while (
#                 self.message_order
#                 and len(self.running_jobs) < dispatch_limit
#             ):

#                 message_id = self.message_order.popleft()

#                 entry = self.pending_message.get(message_id)

#                 if entry is None or not entry["tables"]:
#                     self._cleanup_message(message_id)
#                     continue

#                 # Take one table from this message.
#                 table = entry["tables"].popleft()

#                 response = entry["response"]

#                 entry["running"] += 1

#                 # Put the message at the end of the queue.
#                 # This provides round-robin scheduling.
#                 if entry["tables"]:
#                     self.message_order.append(message_id)

#                 table_name = getattr(
#                     table,
#                     "entity_name",
#                     "unknown"
#                 )

#                 print(
#                     f"Dispatching message {message_id}, "
#                     f"table {table_name}"
#                 )

#                 # Submit exactly one table to the shared pool.
#                 future = loop.run_in_executor(
#                     self.process_pool,
#                     execute_table,
#                     response,
#                     table,
#                 )

#                 self.running_jobs.add(future)

#                 future.add_done_callback(
#                     lambda completed, mid=message_id:
#                     self._job_finished(completed, mid)
#                 )

#                 self._cleanup_message(message_id)

#             # Shutdown only after all pending and running jobs finish.
#             if (
#                 self.closed
#                 and not self.message_order
#                 and not self.running_jobs
#             ):
#                 return

#             self.wakeup.clear()

#             if not self.running_jobs and not self.message_order:
#                 await self.wakeup.wait()
#                 continue

#             # Wait for either:
#             # 1. A worker to finish.
#             # 2. A new message to arrive.
#             if self.running_jobs:

#                 wake_task = asyncio.create_task(
#                     self.wakeup.wait()
#                 )

#                 done, _ = await asyncio.wait(
#                     self.running_jobs | {wake_task},
#                     return_when=asyncio.FIRST_COMPLETED,
#                 )

#                 if wake_task not in done:

#                     wake_task.cancel()

#                     await asyncio.gather(
#                         wake_task,
#                         return_exceptions=True
#                     )

#     # --------------------------------------------------
#     # JOB COMPLETION
#     # --------------------------------------------------

#     def _job_finished(self, future, message_id):

#         self.running_jobs.discard(future)

#         entry = self.pending_message.get(message_id)

#         if entry is not None:
#             entry["running"] -= 1

#         try:

#             result = future.result()

#             print(
#                 f"Message {message_id} table completed: "
#                 f"{result}"
#             )

#         except Exception as exc:

#             print(
#                 f"Table job failed for message "
#                 f"{message_id}: {exc!r}"
#             )

#         self._cleanup_message(message_id)

#         # Wake scheduler immediately.
#         # It can dispatch another table without waiting
#         # for the other workers to finish.
#         self.wakeup.set()

#     # --------------------------------------------------
#     # MESSAGE CLEANUP
#     # --------------------------------------------------

#     def _cleanup_message(self, message_id):

#         entry = self.pending_message.get(message_id)

#         if entry is None:
#             return

#         if (
#             not entry["tables"]
#             and entry["running"] == 0
#         ):

#             del self.pending_message[message_id]

#             print(
#                 f"Message {message_id} fully processed"
#             )

#     # --------------------------------------------------
#     # SHUTDOWN
#     # --------------------------------------------------

#     async def shutdown(self):

#         if self.closed:
#             return

#         self.closed = True

#         self._release_reservation(
#             "scheduler shutdown"
#         )

#         self.wakeup.set()

#         if self.scheduler_task is not None:
#             await self.scheduler_task

#         if (
#             self.reservation_task is not None
#             and not self.reservation_task.done()
#         ):

#             self.reservation_task.cancel()

#             await asyncio.gather(
#                 self.reservation_task,
#                 return_exceptions=True
#             )

#         self.process_pool.shutdown(wait=True)

#         print("TableScheduler shut down")


# # ======================================================
# # TABLE EXECUTION
# # ======================================================

# def execute_table(response, table_data):
#     """
#     Process exactly one table.

#     This function must remain at module level
#     so ProcessPoolExecutor can pickle it.
#     """

#     table_name = getattr(
#         table_data,
#         "entity_name",
#         "unknown"
#     )

#     print(f"Starting table: {table_name}")

#     # --------------------------------------------------
#     # ADLS
#     # --------------------------------------------------

#     if response.destination_type == "adls":

#         if response.load_delta:

#             # Delete and load means fresh initial load.
#             if table_data.load_method == "delete and load":

#                 return load_initial(
#                     response.src_conf,
#                     response.destination_type,
#                     response.destination_config,
#                     table_data,
#                     response.details_for_sts_update,
#                 )

#             # Normal delta load.
#             return load_delta(
#                 response.src_conf,
#                 response.destination_type,
#                 response.destination_config,
#                 table_data,
#                 response.details_for_sts_update,
#             )

#         # Normal initial load.
#         return load_initial(
#             response.src_conf,
#             response.destination_type,
#             response.destination_config,
#             table_data,
#             response.details_for_sts_update,
#         )

#     # --------------------------------------------------
#     # SNOWFLAKE
#     # --------------------------------------------------

#     if response.destination_type == "snowflake":

#         return sap_odata_to_snowflake_connector(
#             response,
#             table_data
#         )

#     raise ValueError(
#         f"Unsupported destination type: "
#         f"{response.destination_type}"
#     )
