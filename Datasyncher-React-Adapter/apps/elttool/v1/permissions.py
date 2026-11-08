from rest_framework import permissions
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import status
from django.core.cache import cache
from django.conf import settings


class CheckTrialPeriod(permissions.BasePermission):
    """
    DRF Permission class to check if trial period is still active.
    Blocks access if trial is expired and redirects to billing.
    
    ✅ OPTIMIZED: Uses caching to avoid repeated database queries
    This eliminates the 2-minute delay by caching trial check results.
    
    Cache Strategy:
    - First request: Checks database and caches result for 5 minutes
    - Subsequent requests: Returns cached result (< 1ms, no DB query)
    - Cache is invalidated when subscription changes (via Company model)
    """
    
    message = "Your trial period has expired. Please select a subscription plan."
    
    def has_permission(self, request, view):
        """
        Check if user has permission to access the view.
        Returns False if trial is expired, True otherwise.
        """
        if not request.user or not request.user.is_authenticated:
            return False
        
        user_id = request.user.user_id
        
        # ✅ OPTIMIZATION 1: Check cache first (5-minute cache)
        # This avoids database queries for repeated requests
        cache_key = f'trial_check_{user_id}'
        cached_result = cache.get(cache_key)
        
        if cached_result is not None:
            # Cache hit - return immediately (no DB query, < 1ms)
            if cached_result == 'expired':
                request.trial_expired_redirect = '/dashboard/plans'
                return False
            return True
        
        # ✅ OPTIMIZATION 2: Try to get company from prefetched user first
        # If user.cmp was prefetched, this won't trigger a query
        company = getattr(request.user, 'cmp', None)
        
        # ✅ OPTIMIZATION 3: If not prefetched, fetch with safe access
        # This ensures we only make ONE query, not multiple
        if company is None:
            try:
                # Try to access it (may trigger query if not prefetched)
                company = request.user.cmp
            except AttributeError:
                # Company doesn't exist - deny access
                if settings.DEBUG:
                    print(f"❌ [CheckTrialPeriod] Company not found for user {user_id}")
                return False
        
        # ✅ OPTIMIZATION 4: Check trial status
        try:
            is_expired = company.is_trial and company.end_date < timezone.now()
            
            if is_expired:
                # Trial expired - cache result and block access
                cache.set(cache_key, 'expired', 300)  # Cache for 5 minutes
                request.trial_expired_redirect = '/dashboard/plans'
                if settings.DEBUG:
                    print(f"⚠️  [CheckTrialPeriod] Trial expired for user {user_id}")
                return False
            
            # Trial active or paid subscription - cache result and allow access
            cache.set(cache_key, 'active', 300)  # Cache for 5 minutes
            if settings.DEBUG:
                status_msg = "trial active" if company.is_trial else "paid subscription"
                print(f"✅ [CheckTrialPeriod] {status_msg} for user {user_id}")
            return True
            
        except Exception as e:
            # On error, deny access for safety
            if settings.DEBUG:
                print(f"❌ [CheckTrialPeriod] Error checking trial period: {e}")
            return False


# ===========================================================================================
# RBAC PERMISSION CLASSES (Django 3.2 Compatible)
# ===========================================================================================

class IsAdministrator(permissions.BasePermission):
    """
    Permission class to check if user has Administrator role.
    
    Administrator role has full access to all system features:
    - Creating and managing users
    - Assigning roles (Analyst, Billing)
    - Creating/editing connectors, destinations, tables
    - Viewing and managing billing and plans
    """
    
    message = "You do not have permission to perform this action. Administrator role required."
    
    def has_permission(self, request, view):
        """
        Check if user is authenticated and has Administrator role.
        """
        print("\n" + "=" * 80)
        print("🔍 [IsAdministrator.has_permission] Checking Administrator permission")
        print(f"   View: {view.__class__.__name__}")
        print(f"   Method: {request.method}")
        print(f"   Path: {request.path}")
        
        if not request.user or not request.user.is_authenticated:
            print(f"   ❌ User not authenticated")
            print("=" * 80 + "\n")
            return False
        
        print(f"   User: {request.user.company_email} (ID: {request.user.user_id})")
        
        # Check if user has Administrator role
        try:
            user_role = request.user.roles
            print(f"   User role object: {user_role}")
            print(f"   User role name: {user_role.role_name if user_role else 'None'}")
            
            if user_role and user_role.role_name == 'Administrator':
                print(f"   ✅ Permission GRANTED - User has Administrator role")
                print("=" * 80 + "\n")
                return True
            
            print(f"   ❌ Permission DENIED - User role is '{user_role.role_name if user_role else 'None'}', not Administrator")
            print("=" * 80 + "\n")
            return False
        except AttributeError as e:
            print(f"   ❌ Error accessing user role: {e}")
            print("=" * 80 + "\n")
            return False


class IsAnalystOrAdmin(permissions.BasePermission):
    """
    Permission class to check if user has Analyst or Administrator role.
    
    Analyst role can:
    - View connectors
    - View destinations
    - View tables
    - Add and edit tables
    - View sync status, logs, metrics
    
    Restrictions:
    - ❌ Cannot create/edit/delete connectors
    - ❌ No access to billing or plans
    """
    
    message = "You do not have permission to perform this action. Analyst or Administrator role required."
    
    def has_permission(self, request, view):
        """
        Check if user is authenticated and has Analyst or Administrator role.
        """
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Check if user has Analyst or Administrator role
        try:
            user_role = request.user.roles
            if user_role and user_role.role_name in ['Analyst', 'Administrator']:
                if settings.DEBUG:
                    print(f"✅ [IsAnalystOrAdmin] User {request.user.user_id} has {user_role.role_name} role")
                return True
            
            if settings.DEBUG:
                print(f"❌ [IsAnalystOrAdmin] User {request.user.user_id} does not have Analyst or Administrator role (has: {user_role.role_name if user_role else 'None'})")
            return False
            
        except Exception as e:
            if settings.DEBUG:
                print(f"❌ [IsAnalystOrAdmin] Error checking role: {e}")
            return False


class IsBillingOrAdmin(permissions.BasePermission):
    """
    Permission class to check if user has Billing or Administrator role.
    
    Billing role has access only to:
    - Billing page
    - Subscription plans
    - Invoices
    - Payment history
    
    Restrictions:
    - ❌ No access to connectors, destinations, or data views
    - ❌ No user management
    - ❌ No system configuration
    """
    
    message = "You do not have permission to perform this action. Billing or Administrator role required."
    
    def has_permission(self, request, view):
        """
        Check if user is authenticated and has Billing or Administrator role.
        """
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Check if user has Billing or Administrator role
        try:
            user_role = request.user.roles
            if user_role and user_role.role_name in ['Billing', 'Administrator']:
                if settings.DEBUG:
                    print(f"✅ [IsBillingOrAdmin] User {request.user.user_id} has {user_role.role_name} role")
                return True
            
            if settings.DEBUG:
                print(f"❌ [IsBillingOrAdmin] User {request.user.user_id} does not have Billing or Administrator role (has: {user_role.role_name if user_role else 'None'})")
            return False
            
        except Exception as e:
            if settings.DEBUG:
                print(f"❌ [IsBillingOrAdmin] Error checking role: {e}")
            return False


class IsPlatformSuperuser(permissions.BasePermission):
    """Platform staff only (cross-tenant ops / admin app). Not company Administrator RBAC."""

    message = "Platform superuser access required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "is_superuser", False)
        )


class IsAdminForWrite(permissions.BasePermission):
    """
    Permission class that allows:
    - Read access (GET, HEAD, OPTIONS) for Analyst and Administrator
    - Write access (POST, PUT, PATCH, DELETE) for Administrator only
    
    This is useful for endpoints where Analysts can view but not modify.
    """
    
    message = "You do not have permission to perform this action. Administrator role required for modifications."
    
    def has_permission(self, request, view):
        """
        Check permissions based on request method.
        """
        if not request.user or not request.user.is_authenticated:
            return False
        
        try:
            user_role = request.user.roles
            if not user_role:
                return False
            
            # Read-only methods: allow Analyst and Administrator
            if request.method in permissions.SAFE_METHODS:
                if user_role.role_name in ['Analyst', 'Administrator']:
                    if settings.DEBUG:
                        print(f"✅ [IsAdminForWrite] User {request.user.user_id} has read access ({user_role.role_name})")
                    return True
            
            # Write methods: allow Administrator only
            else:
                if user_role.role_name == 'Administrator':
                    if settings.DEBUG:
                        print(f"✅ [IsAdminForWrite] User {request.user.user_id} has write access (Administrator)")
                    return True
                else:
                    if settings.DEBUG:
                        print(f"❌ [IsAdminForWrite] User {request.user.user_id} denied write access (has: {user_role.role_name})")
                    return False
            
            return False
            
        except Exception as e:
            if settings.DEBUG:
                print(f"❌ [IsAdminForWrite] Error checking role: {e}")
            return False
class CanManageTransform(permissions.BasePermission):
    """
    Read: Analyst or Administrator.
    Write (upload script, save, activate): Administrator or user with transform_manage permission.
    """

    message = "You do not have permission to manage entity transforms."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.method in permissions.SAFE_METHODS:
            try:
                role = request.user.roles
                return role and role.role_name in ("Analyst", "Administrator")
            except Exception:
                return False

        try:
            role = request.user.roles
            if role and role.role_name == "Administrator":
                return True
            return request.user.has_perm("elttool.transform_manage")
        except Exception:
            return False


from rest_framework.permissions import BasePermission
import hmac


class IsInternalService(BasePermission):
    """
    Cluster-internal callers must present X-INTERNAL-KEY.
    Fails closed when INTERNAL_API_KEY is unset or empty.
    """

    message = "Internal service authentication required."

    def has_permission(self, request, view):
        expected = getattr(settings, "INTERNAL_API_KEY", None)
        provided = request.headers.get("X-INTERNAL-KEY")
        if not expected or not provided:
            return False
        return hmac.compare_digest(str(provided), str(expected))


class IsInternalProxyAgentGateway(BasePermission):
    """
    Cluster-internal progress ingest from proxy-agent-gateway.

    Requires IsInternalService key plus trusted X-Agent-UID set by the gateway
    from the verified client certificate CN (never from untrusted JSON).
    """

    message = "Proxy agent gateway authentication required."

    def has_permission(self, request, view):
        if not IsInternalService().has_permission(request, view):
            return False
        agent_uid = (request.headers.get("X-Agent-UID") or "").strip()
        if not agent_uid:
            return False
        request.proxy_agent_uid = agent_uid
        return True
