from azure.storage.blob import BlobServiceClient
from fastapi import HTTPException

class MicrosoftDynamics365Source:
    """
    Class to handle Microsoft Dynamics 365 Source Connection.
    """

    @staticmethod
    def list_limited_files_in_container(container_client, max_count=5):
        """
        List up to `max_count` files in the specified container.
        """
        try:
            blobs = container_client.list_blobs()
            file_names = []
            for blob in blobs:
                if not blob.name.endswith('/'):  # Exclude trailing slashes (directories)
                    file_names.append(blob.name)
                if len(file_names) >= max_count:  # Stop once the limit is reached
                    break
            return file_names
        except Exception as e:
            raise Exception(f"An error occurred while listing files: {e}")

    @staticmethod
    async def validate_connection(data):
        """
        Validate Microsoft Dynamics connection and list up to 5 files in the container.
        """
        microsoft_dynamics = data.MicrosoftDynamics365_FO
        container_name = microsoft_dynamics.get("container_name")
        connection_string = microsoft_dynamics.get("connection_string")

        if not connection_string or not container_name:
            raise HTTPException(
                status_code=400,
                detail="Missing required parameters: connection_string or container_name"
            )

        try:
            blob_service_client = BlobServiceClient.from_connection_string(connection_string)
            container_client = blob_service_client.get_container_client(container_name)
        except Exception as e:
            raise HTTPException(
                status_code=401,
                detail=f"Failed to create BlobServiceClient: {e}"
            )

        try:
            files_in_container = MicrosoftDynamics365Source.list_limited_files_in_container(container_client, max_count=5)
            if files_in_container:
                return {
                    "body": "Connection established successfully",
                    "files": files_in_container,
                    "status_code": 200
                }
            else:
                raise HTTPException(
                    status_code=404,
                    detail="No files found in the specified container"
                )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list files in container: {e}"
            )
