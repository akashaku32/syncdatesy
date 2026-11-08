from azure.identity import ClientSecretCredential
from azure.storage.filedatalake import DataLakeServiceClient
from azure.core.exceptions import ResourceNotFoundError

def adls_connection(TENANT_ID,CLIENT_ID,CLIENT_SECRET,CONTAINER_NAME,STORAGE_ACCOUNT_NAME):

    """
    Creates and validates an ADLS connection.
    Raises an exception if authentication fails.
    """

    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    )

    service_client = DataLakeServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT_NAME}.dfs.core.windows.net",
        credential=credential
    )

    # Validate the connection
    filesystem_client = service_client.get_file_system_client(CONTAINER_NAME)
    next(filesystem_client.get_paths(), None)

    return service_client

def write_adls(service_client,container_name,directory,filename,data=None):

    file_system_client = service_client.get_file_system_client(
        file_system=container_name
    )

    try:
        directory_client = file_system_client.create_directory(
            directory
        )
    except Exception:
        directory_client = file_system_client.get_directory_client(
            directory
        )

    file_client = directory_client.get_file_client(
        filename
    )
    
    if data:
        file_client.upload_data(
                data,
                overwrite=True
            )
    else:
        # for creating empty success file 
        file_client.create_file()      
        file_client.flush_data(0) 

def delete_adls_directory(service_client,container_name,directory):

    """
    Deletes an ADLS directory and all of its contents if it exists.
    """

    file_system_client = service_client.get_file_system_client(
        file_system=container_name
    )

    directory_client = file_system_client.get_directory_client(directory)

    try:
        directory_client.delete_directory()
        print(f"Deleted ADLS directory: {directory}")
    except ResourceNotFoundError:
        print(f"Directory '{directory}' does not exist.")
    except Exception as e:
        print(f"Failed to delete directory '{directory}': {e}")
        raise

def delete_adls_file(service_client,container_name,directory, file_name):

    """
    Deletes a single file inside an ADLS directory if it exists.
    """

    file_system_client = service_client.get_file_system_client(
        file_system=container_name
    )

    file_path = f"{directory.rstrip('/')}/{file_name}"
    file_client = file_system_client.get_file_client(file_path)
 
    try:
        file_client.delete_file()
        print(f"Deleted ADLS file: {file_path}")
    except ResourceNotFoundError:
        print(f"File '{file_path}' does not exist.")
    except Exception as e:
        print(f"Failed to delete file '{file_path}': {e}")
        raise