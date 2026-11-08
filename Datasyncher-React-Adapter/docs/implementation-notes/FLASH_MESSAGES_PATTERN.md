# Professional Flash Message Pattern

## ❌ Old Approach (Unprofessional)
```typescript
// Passing errors in URL - BAD PRACTICE ❌
redirect('/dashboard/destinations?error=Something went wrong&success=...')

// React extracts from URL query params
const error = new URLSearchParams(location.search).get('error')
```

**Problems:**
- Exposes error messages in browser history
- URL encoding issues with special characters
- Unprofessional user experience
- Security concerns (sensitive info in URL)
- Cluttered, ugly URLs

---

## ✅ New Approach (Professional)

### **Backend (Django)**

#### 1. Store messages in session (not URL)
```python
# apps/elttool/v1/views.py

# ✅ GOOD - Store in session
request.session['oauth_success'] = 'Authorization completed successfully'
request.session['oauth_error'] = 'Authentication failed. Please try again.'
request.session['oauth_warning'] = 'Partial success - destination not found'

# Then redirect to clean URL (no query params)
return redirect('/dashboard/destinations')
```

#### 2. API endpoint to retrieve messages
```python
class FlashMessageAPIView(APIView):
    """
    API endpoint for React to fetch flash messages.
    Messages are automatically cleared after retrieval (flash behavior).
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        messages = {
            'success': request.session.pop('oauth_success', None),
            'error': request.session.pop('oauth_error', None),
            'warning': request.session.pop('oauth_warning', None),
        }
        
        # Clean up None values
        messages = {k: v for k, v in messages.items() if v is not None}
        
        return Response(messages, status=status.HTTP_200_OK)
```

**URL:** `GET /api/v1/flash-messages/`

---

### **Frontend (React/TypeScript)**

#### 1. Create a Flash Message Hook

```typescript
// src/hooks/useFlashMessages.ts
import { useEffect } from 'react';
import { useToast } from '@/hooks/use-toast';
import axiosInstance from '@/utils/axiosInstance';

export const useFlashMessages = () => {
  const { toast } = useToast();

  useEffect(() => {
    const fetchFlashMessages = async () => {
      try {
        const response = await axiosInstance.get('/flash-messages/');
        const messages = response.data;

        // Display messages using your toast/notification system
        if (messages.success) {
          toast({
            title: 'Success',
            description: messages.success,
            variant: 'success',
          });
        }

        if (messages.error) {
          toast({
            title: 'Error',
            description: messages.error,
            variant: 'destructive',
          });
        }

        if (messages.warning) {
          toast({
            title: 'Warning',
            description: messages.warning,
            variant: 'warning',
          });
        }
      } catch (error) {
        // Silently fail - no flash messages to display
        console.debug('No flash messages available');
      }
    };

    fetchFlashMessages();
  }, []); // Run once on component mount
};
```

#### 2. Use in Components After OAuth Redirect

```typescript
// src/components/dashboard/Destinations.tsx
import { useFlashMessages } from '@/hooks/useFlashMessages';

export const Destinations = () => {
  // Automatically fetch and display flash messages on mount
  useFlashMessages();

  return (
    <div>
      <h1>Destinations</h1>
      {/* Your component content */}
    </div>
  );
};
```

#### 3. Use in Connection Detail Pages

```typescript
// src/components/dashboard/ConnectionDetail.tsx
import { useFlashMessages } from '@/hooks/useFlashMessages';

export const ConnectionDetail = () => {
  useFlashMessages();

  return (
    <div>
      <h1>Connection Overview</h1>
      {/* Your component content */}
    </div>
  );
};
```

---

## How It Works (Flow Diagram)

```
1. User clicks "Authorize Salesforce"
   ↓
2. Django redirects to Salesforce OAuth
   ↓
3. User authorizes on Salesforce
   ↓
4. Salesforce redirects back to Django callback
   ↓
5. Django processes OAuth:
   - Exchanges code for tokens
   - Saves configuration
   - Stores flash message in session ✅
   - Redirects to CLEAN URL (no query params) ✅
   ↓
6. React app loads
   ↓
7. useFlashMessages() hook runs:
   - Calls GET /api/v1/flash-messages/
   - Retrieves messages from session
   - Django automatically clears messages (flash behavior)
   - React displays toast notifications ✅
   ↓
8. User sees clean URL + professional toast notification 🎉
```

---

## Benefits of This Approach

### ✅ Professional
- Clean URLs without query parameters
- Industry-standard pattern (used by Rails, Laravel, Django admin, etc.)
- Proper separation of concerns

### ✅ Secure
- Messages not exposed in browser history
- Not bookmarkable with error states
- Session-based (server-side storage)

### ✅ User Experience
- Beautiful toast notifications instead of URL params
- Messages auto-dismiss
- No URL clutter
- Works with browser back/forward buttons

### ✅ Developer Experience
- Simple API: `request.session['oauth_success'] = 'message'`
- Automatic cleanup (messages cleared after retrieval)
- Consistent pattern across entire application
- Easy to test and debug

---

## Implementation Checklist

### Backend (Already Done ✅)
- [x] Update all `redirect()` calls to use session instead of query params
- [x] Create `FlashMessageAPIView` API endpoint
- [x] Add `/api/v1/flash-messages/` to URLs
- [x] Test with OAuth callbacks

### Frontend (TODO)
- [ ] Create `useFlashMessages()` hook
- [ ] Add hook to destination listing page
- [ ] Add hook to connector listing page
- [ ] Add hook to connection detail pages
- [ ] Remove any URL query param parsing for errors/success
- [ ] Test OAuth flow end-to-end

---

## Example API Response

**Request:**
```http
GET /api/v1/flash-messages/
Authorization: Bearer <token>
```

**Response (with message):**
```json
{
  "success": "Authorization completed successfully"
}
```

**Response (no messages):**
```json
{}
```

**Response (multiple messages):**
```json
{
  "error": "Token exchange failed. Please try again.",
  "warning": "Connection partially configured"
}
```

---

## Migration Notes

### What Changed
1. **All OAuth callbacks** now store messages in session instead of URL
2. **All redirects** are now clean URLs (no `?error=...` or `?success=...`)
3. **New API endpoint** at `/api/v1/flash-messages/` for React to fetch messages

### Backward Compatibility
- Old URLs with query params still work (not parsing them)
- Gradually remove any React code that reads `?error=` or `?success=` from URLs

### Testing
```python
# Django test
response = client.get('/api/v1/salesforce/callback/?code=123&state=abc')
assert response.url == '/dashboard/destinations'  # Clean URL ✅
assert client.session['oauth_success'] == 'Authorization completed successfully'

# React test
const { result } = renderHook(() => useFlashMessages())
await waitFor(() => {
  expect(toast).toHaveBeenCalledWith({
    title: 'Success',
    description: 'Authorization completed successfully'
  })
})
```

---

## References

This pattern is inspired by:
- **Ruby on Rails** flash messages
- **Laravel** session flash
- **Django contrib.messages** framework
- **Express.js** connect-flash middleware

All modern web frameworks use this session-based approach instead of URL parameters.


