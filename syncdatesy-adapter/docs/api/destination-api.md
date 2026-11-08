# Destination API Documentation

This document provides detailed information about the API endpoints used for managing destinations in the Datasyncher application.

## Base Configuration
- **Base URL**: `{{baseUrl}}` (e.g., `https://qa.datasyncher.com/`)
- **API Version**: `{{version}}` (e.g., `api/v1`)
- **Full Prefix**: `{{baseUrl}}{{version}}/`

---

## 1. List Active Destination Configurations
Returns a list of active destination configurations for the logged-in user's company.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/`
- **Method**: `GET`
- **Description**: Fetches all active destination configurations. Supports optional `name` filter and pagination.
- **Query Parameters**:
  - `name` (optional): Filter destinations by name (case-insensitive).
  - `page` (optional): If present, returns paginated results.

---

## 2. List Available Destination Types
Lists all supported destination types (e.g., Snowflake, PostgreSQL).

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/all/`
- **Method**: `GET`
- **Description**: Fetches all available destination models. 
- **Query Parameters**:
  - `name` (optional): Filter destination types by name.

---

## 3. Get Destination Schema
Retrieves the dynamic form schema for a specific destination type.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/schema/<destination_name>/`
- **Method**: `GET`
- **Description**: Returns the fields required to configure the specified destination.
- **Example Endpoint**: `{{baseUrl}}{{version}}/destinations/schema/Snowflake/`
- **Notes**: Calling this endpoint stores the selected destination in the session, which is required for the subsequent "Create" operation.

---

## 4. Get Destination Config Form (Create/Edit)
Retrieves the configuration form for a specific destination, pre-filled if an ID is provided.

- **Endpoint**: 
  - Create: `{{baseUrl}}{{version}}/destinations/config/<destination_name>/`
  - Edit: `{{baseUrl}}{{version}}/destinations/config/<destination_name>/<dst_config_id>/`
- **Method**: `GET`
- **Description**: Returns form fields with current values (if editing) and metadata like `read_only` status based on existing data transfers.

---

## 5. Destination Config Status
Retrieves the status and parsed configuration data of a specific destination.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/status/<dst_config_id>/`
- **Method**: `GET`
- **Description**: Returns details about whether a connection exists and the full `config_data`.

---

## 6. Destination Config Detail
Retrieves comprehensive details of a specific destination configuration.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/detail/<dst_config_id>/`
- **Method**: `GET`
- **Description**: Similar to Status, but includes serializer-formatted destination details and field information.

---

## 7. Select Destination
Selects a destination for the current user session.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/select-destination/`
- **Method**: `GET` | `POST`
- **GET**: Lists available destination configurations for selection.
- **POST Description**: Saves the selected destination ID to the session.
- **POST JSON Body**:
```json
{
    "destination": 123
}
```

---

## 8. Delete Destination
Deletes (deactivates) a destination configuration.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/delete/<dst_config_id>/`
- **Method**: `GET` | `DELETE`
- **GET**: Checks if the destination can be deleted (no active connections).
- **DELETE Description**: Soft-deletes the destination by setting `is_active=False`.

---

## 9. Change Destination Name
Renames an existing destination configuration.

- **Endpoint**: `{{baseUrl}}{{version}}/change-destination-name/<dst_config_id>/`
- **Method**: `PUT`
- **JSON Body**:
```json
{
    "new_destination_name": "My New Snowflake Prod"
}
```

---

## 10. Create Destination Configuration
Creates and authorizes a new destination configuration.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/create/`
- **Method**: `POST`
- **Description**: Validates and saves a new destination config. Requires `config_data`. The `dst` type is typically retrieved from the session (set in Endpoint #3).

### Examples: JSON Body

#### Snowflake (Standard Auth)
```json
{
    "name": "Snowflake Destination",
    "config_data": {
        "destination_name": "Snowflake Destination",
        "authentication_type": "password",
        "account_id": "xyz123.us-east-1",
        "username": "admin",
        "password": "secure_password",
        "warehouse": "COMPUTE_WH",
        "database": "DATASYNCHER_DB",
        "schema": "PUBLIC"
    }
}
```

#### Snowflake (Key Pair Auth)
```json
{
    "name": "Snowflake KeyPair",
    "config_data": {
        "destination_name": "Snowflake KeyPair",
        "authentication_type": "key_pair",
        "account_id": "xyz123.us-east-1",
        "username": "svc_user",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...",
        "passphrase": "my_passphrase",
        "warehouse": "COMPUTE_WH",
        "database": "DATASYNCHER_DB",
        "schema": "PUBLIC"
    }
}
```

#### PostgreSQL
```json
{
    "name": "Local Postgres",
    "config_data": {
        "destination_name": "Local Postgres",
        "host": "localhost",
        "port": 5432,
        "database": "my_db",
        "username": "postgres",
        "password": "password",
        "schema": "public"
    }
}
```

---

## 11. Update Destination Configuration
Updates and re-authorizes an existing destination configuration.

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/update/<dst_config_id>/`
- **Method**: `PUT`
- **Description**: Updates the configuration. Some fields may be `read_only` if data transfers have already occurred.

### JSON Body Example
```json
{
    "name": "Updated Snowflake Name",
    "config_data": {
        "destination_name": "Updated Snowflake Name",
        "warehouse": "NEW_WH",
        "database": "DATASYNCHER_DB",
        "schema": "PUBLIC"
    }
}
```

---

## 12. Test Destination Connection
Tests the connection of a destination using provided form data (unsaved changes).

- **Endpoint**: `{{baseUrl}}{{version}}/destinations/test/<dst_config_id>/`
- **Method**: `POST`
- **Description**: Validates the provided `config_data` against the destination service without saving it. This uses unsaved form data.

### JSON Body Example (Testing Unsaved Host)
```json
{
    "config_data": {
        "account_id": "new_account.region",
        "username": "admin",
        "password": "new_password",
        "warehouse": "COMPUTE_WH",
        "database": "DB",
        "schema": "PUBLIC"
    }
}
```
