
import requests
from odata_functions import load_initial,load_delta
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import time


def loadinitialforall(src_config,destination_type,destination_config,sources,details_for_status_update):
    """
    """
    print("started=======================================",datetime.now())
    remaining = sources
    if destination_type == "adls":
        for attempt in range(0,3):
            with ThreadPoolExecutor(max_workers=4) as pool:

                results = list(
                    pool.map(
                        lambda src: load_initial(
                            src_config,
                            destination_type,
                            destination_config,
                            src,
                            details_for_status_update
                        ),
                        remaining
                    )
                )

            # Keep only the sources that failed
            remaining = [
                src
                for src, result in zip(remaining, results)
                if result is None
            ]

            if not remaining:
                break

            print(f"Retrying {len(remaining)} failed sources...")
            time.sleep(2)
            
        if remaining:
            print("Still failed after 3 attempts:", remaining)

    elif destination_type == "snowflake":
        pass








    print("ended==================init=====================",datetime.now())

def load_delta_forall(src_config, destination_type, destination_config, sources,details_for_sts_update):
    """
    Execute delta/delete-and-load for all sources in parallel with retries.
    """
    print("started=======================================", datetime.now())

    remaining = sources

    for attempt in range(3):

        with ThreadPoolExecutor(max_workers=4) as pool:

            results = list(
                pool.map(
                    lambda src: (
                        load_initial(
                            src_config,
                            destination_type,
                            destination_config,
                            src,
                            details_for_sts_update
                        )
                        if src.load_method == "delete and load"
                        else load_delta(
                            src_config,
                            destination_type,
                            destination_config,
                            src,
                            details_for_sts_update
                        )
                    ),
                    remaining,
                )
            )

        # Keep only failed sources (functions return None on failure)
        remaining = [
            src
            for src, result in zip(remaining, results)
            if result is None
        ]

        if not remaining:
            break

        print(f"Retrying {len(remaining)} failed sources... (Attempt {attempt + 2}/3)")
        time.sleep(2)

    if remaining:
        print("Still failed after 3 attempts:", remaining)

    print("ended=======================================", datetime.now())


        