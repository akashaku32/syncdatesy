# Status Filter Implementation

## Overview
Implemented an additional Status Filter on top of the existing Time Filter for the Connection Activity Logs API. The Status Filter works within the already selected time range and applies job-level filtering.

## Key Features

### 1. Filtering Order
1. **Time Filter First**: Applies time range filtering (Last 2 days, 7 days, 30 days, etc.)
2. **Status Filter Second**: Applies job-level status filtering within the time-filtered results

### 2. Status Determination Logic (Job Level)
Each Job contains multiple Tables. The Job status is derived from its table results:

- **Completed**: A Job appears under "Completed" ONLY when ALL tables in that Job are successful
  - 5/5 success → Completed
  - If even one table failed or skipped → NOT Completed

- **Failed**: A Job appears under "Failed" if ANY table in that Job failed
  - Partial failure (e.g., 2 success, 3 failed)
  - Full failure (all tables failed)

### 3. Frontend Behavior
- **Left Panel**: Shows Job-level messages (one entry per Job) - filtered by Status Filter
- **Right Panel**: Shows Table-level details for the selected Job - NOT filtered by Status Filter
- Status filtering is strictly Job-level, even though table details are shown after clicking

## API Endpoint

### URL
```
GET /api/v1/connection/<int:connection_id>/activity/
```

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | integer | No | Filter logs from N days ago (e.g., 7 for last 7 days) |
| `hour` | integer | No | Filter logs from N hours ago (e.g., 1 for last 1 hour) |
| `status` | string | No | Filter jobs by status. Valid values: `completed`, `failed` |

### Example Requests

#### Get all logs from last 2 days (default)
```
GET /api/v1/connection/123/activity/
```

#### Get logs from last 7 days
```
GET /api/v1/connection/123/activity/?days=7
```

#### Get logs from last 30 days
```
GET /api/v1/connection/123/activity/?days=30
```

#### Get logs from last 24 hours
```
GET /api/v1/connection/123/activity/?hour=24
```

#### Get only completed jobs from last 7 days
```
GET /api/v1/connection/123/activity/?days=7&status=completed
```

#### Get only failed jobs from last 2 days
```
GET /api/v1/connection/123/activity/?days=2&status=failed
```

### Response Format
```json
{
  "logs": [
    {
      "log_id": 12345,
      "message": "Migration completed successfully",
      "user_name": "John Doe",
      "timestamp": "2026-02-10T10:30:00Z",
      "status": "S",
      "session_id": 456,
      "is_clickable": true,
      "migration_id": 789,
      "log_type": "connection_activity",
      "trigger_type": "Manual"
    }
  ],
  "connection": {
    "connection_id": 123,
    "readable_time_frequency": "1 hour",
    "time_frequency": "60",
    "status": "A"
  },
  "cmp_id": 456
}
```

## Implementation Details

### Files Modified
- `apps/elttool/v1/views.py` - Added status filtering logic to `ConnectionLogsAPIView`

### New Function Added
```python
def determine_job_status(job_name, connection_id):
    """
    Determine the overall status of a job based on its table results.
    
    Logic:
    - COMPLETED: All tables must be successful
    - FAILED: Any table failed (includes partial failure and full failure)
    """
```

### Validation
- Status parameter validation with clear error messages
- Case-insensitive status comparison
- Proper error handling for database queries

## Frontend Integration Guide

### 1. Status Filter Component
Create a dropdown/select component with options:
- All Statuses (no filter)
- Completed
- Failed

### 2. API Integration
```javascript
// Example implementation
const fetchActivityLogs = async (connectionId, timeFilter, statusFilter) => {
  const params = new URLSearchParams();
  
  // Add time filter
  if (timeFilter.days) {
    params.append('days', timeFilter.days);
  } else if (timeFilter.hour) {
    params.append('hour', timeFilter.hour);
  }
  
  // Add status filter
  if (statusFilter && statusFilter !== 'all') {
    params.append('status', statusFilter);
  }
  
  const response = await fetch(`/api/v1/connection/${connectionId}/activity/?${params}`);
  return response.json();
};

// Usage examples
fetchActivityLogs(123, { days: 2 }, 'completed');     // Last 2 days, completed only
fetchActivityLogs(123, { days: 7 }, 'failed');        // Last 7 days, failed only
fetchActivityLogs(123, { days: 30 }, 'all');          // Last 30 days, all statuses
```

### 3. State Management
```javascript
const [timeFilter, setTimeFilter] = useState({ days: 2 });  // Default: Last 2 days
const [statusFilter, setStatusFilter] = useState('all');    // Default: All statuses
const [logs, setLogs] = useState([]);

useEffect(() => {
  fetchActivityLogs(connectionId, timeFilter, statusFilter)
    .then(data => setLogs(data.logs));
}, [connectionId, timeFilter, statusFilter]);
```

## Testing Scenarios

### Scenario 1: Time + Status Filtering
- Time Filter: Last 2 Days
- Status Filter: Completed
- Expected: Only jobs where ALL tables succeeded in the last 2 days

### Scenario 2: Partial Failure
- Job A: 3 tables completed, 2 tables failed
- Status Filter: Completed
- Expected: Job A should NOT appear (because not all tables completed)

### Scenario 3: Mixed Results
- Time Filter: Last 7 days
- Status Filter: Failed
- Expected: Jobs with ANY failed tables in the last 7 days

## Error Handling

### Invalid Status Parameter
```json
{
  "message": "Invalid status parameter. Valid values: ['completed', 'failed']"
}
```

### Invalid Time Parameters
```json
{
  "message": "Invalid days parameter"
}
```
or
```json
{
  "message": "Invalid hours parameter"
}
```

## Performance Considerations

1. **Database Indexes**: Existing indexes on `connection`, `time`, and `job_name` fields are utilized
2. **Query Optimization**: Status determination happens per job, not per table in the response loop
3. **Caching**: Consider implementing caching for frequently accessed time/status combinations

## Future Enhancements

1. Add "In Progress" status filter option
2. Implement pagination for large result sets
3. Add sorting options (by time, by status, by user)
4. Include status counts in response for filter badges