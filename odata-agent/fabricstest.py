"""
Create a Microsoft Fabric notebook from a local Python file.

Setup:
    pip install requests azure-identity

Usage:
    Fill in the constants below (credentials, WORKSPACE_ID, NOTEBOOK_NAME),
    edit NOTEBOOK_CODE, then run:
        python create_fabric_notebook.py

Cells: separate cells in NOTEBOOK_CODE with "# %%" lines.
If there are no markers, the whole code becomes a single cell.

Auth: service principal (ClientSecretCredential). Set TENANT_ID, CLIENT_ID
and CLIENT_SECRET in the constants below.
"""
import base64
import json
import os
import re
import time

import requests
from azure.identity import ClientSecretCredential

# ---- Set FABRIC_* env vars or fill placeholders for local runs ----
TENANT_ID = os.environ.get("FABRIC_TENANT_ID", "")
CLIENT_ID = os.environ.get("FABRIC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("FABRIC_CLIENT_SECRET", "")

WORKSPACE_ID = "2f543223-c686-4f51-90c7-1c1bfe167ab8"
NOTEBOOK_NAME = "My Notebook"
NOTEBOOK_DESCRIPTION = ""

# Notebook content. Each "# %%" line starts a new cell.
NOTEBOOK_CODE = '''
# %%
print("Hello from Microsoft Fabric!")

# %%
import pandas as pd

df = pd.DataFrame({"id": [1, 2, 3], "name": ["A", "B", "C"]})
print(df)

# %%
print("Row count:", len(df))
'''
# -----------------------------------------------------------

FABRIC_API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"


def py_to_ipynb(source: str) -> dict:
    """Convert a .py file with '# %%' cell markers into an ipynb dict."""
    chunks = re.split(r"^# %%.*$", source, flags=re.MULTILINE)
    chunks = [c.strip("\n") for c in chunks if c.strip()]
    cells = [
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": chunk.splitlines(keepends=True),
        }
        for chunk in chunks
    ]
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": cells,
        "metadata": {
            "language_info": {"name": "python"},
            "kernel_info": {"name": "synapse_pyspark"},
        },
    }


def build_payload(name: str, notebook: dict, description: str = "") -> dict:
    content = base64.b64encode(json.dumps(notebook).encode("utf-8")).decode("ascii")
    return {
        "displayName": name,
        "description": description,
        "type": "Notebook",
        "definition": {
            "format": "ipynb",
            "parts": [
                {
                    "path": "notebook-content.ipynb",
                    "payload": content,
                    "payloadType": "InlineBase64",
                }
            ],
        },
    }


def create_notebook(workspace_id: str, payload: dict) -> dict:
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    )
    token = credential.get_token(SCOPE).token
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resp = requests.post(
        f"{FABRIC_API}/workspaces/{workspace_id}/items",
        headers=headers,
        json=payload,
        timeout=60,
    )

    # 201 = created immediately, 202 = long-running operation
    if resp.status_code == 201:
        return resp.json()
    if resp.status_code != 202:
        raise RuntimeError(f"Create failed ({resp.status_code}): {resp.text}")

    op_url = resp.headers["Location"]
    while True:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.get(op_url, headers=headers, timeout=60)
        resp.raise_for_status()
        status = resp.json().get("status")
        if status == "Succeeded":
            result = requests.get(op_url.rstrip("/") + "/result", headers=headers, timeout=60)
            return result.json() if result.ok else resp.json()
        if status == "Failed":
            raise RuntimeError(f"Operation failed: {resp.text}")


def main():
    notebook = py_to_ipynb(NOTEBOOK_CODE)

    item = create_notebook(
        WORKSPACE_ID, build_payload(NOTEBOOK_NAME, notebook, NOTEBOOK_DESCRIPTION)
    )
    print("Created notebook:", json.dumps(item, indent=2))


if __name__ == "__main__":
    main()

# import requests
# from azure.identity import ClientSecretCredential

# cred = ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET)
# token = cred.get_token("https://api.fabric.microsoft.com/.default").token
# r = requests.get("https://api.fabric.microsoft.com/v1/workspaces",
#                  headers={"Authorization": f"Bearer {token}"})
# print(r.status_code, r.text)