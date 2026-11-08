"""
RBAC API Views for User Management and Role-Based Access Control

This module provides REST API endpoints for:
- User management (create, list, update users)
- Role assignment
- User information retrieval

All endpoints enforce role-based permissions at the backend level.
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from django.db import transaction
from django.conf import settings

from apps.elttool.models import Role, Company
from apps.elttool.v1.permissions import IsAdministrator, IsAnalystOrAdmin, IsBillingOrAdmin
from apps.elttool.v1.serializers import UserSerializer, UserInfoSerializer

User = get_user_model()


class UserManagementAPIView(APIView):
    """
    User Management API (Administrator Only)
    
    Endpoints:
    - GET /api/v1/users - List all users in the company
    - POST /api/v1/users - Create a new user (Analyst or Billing)
    - PATCH /api/v1/users/{id} - Update user role
    - DELETE /api/v1/users/{id} - Deactivate user
    """
    
    permission_classes = [IsAuthenticated, IsAdministrator]
    
    def get(self, request):
        """List all users in the company."""
        try:
            company = request.user.cmp
            users = User.objects.filter(cmp=company).select_related('roles')
            
            user_data = []
            for user in users:
                user_data.append({
                    'user_id': user.user_id,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'company_email': user.company_email,
                    'role': user.roles.role_name if user.roles else None,
                    'role_id': user.roles.role_id if user.roles else None,
                    'is_active': user.is_active,
                    'date_joined': user.date_joined,
                })
            
            return Response({
                'users': user_data
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            if settings.DEBUG:
                print(f"❌ [UserManagementAPIView.get] Error: {e}")
            return Response({
                'error': 'INTERNAL_ERROR',
                'message': 'Failed to retrieve users'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def post(self, request):
        """Create a new user (Analyst or Billing role)."""
        try:
            # Validate required fields
            required_fields = ['first_name', 'last_name', 'company_email', 'password', 'role_id']
            for field in required_fields:
                if field not in request.data:
                    return Response({
                        'error': 'VALIDATION_ERROR',
                        'message': f'Missing required field: {field}'
                    }, status=status.HTTP_400_BAD_REQUEST)
            
            # Validate role_id (only Analyst or Billing can be created)
            role_id = request.data.get('role_id')
            if role_id not in [2, 3]:  # 2=Analyst, 3=Billing
                return Response({
                    'error': 'INVALID_ROLE',
                    'message': 'Invalid role. Only Analyst (2) or Billing (3) roles can be assigned.'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Check if email already exists
            email = request.data.get('company_email')
            if User.objects.filter(company_email=email).exists():
                return Response({
                    'error': 'EMAIL_EXISTS',
                    'message': 'A user with this email already exists'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get role
            try:
                role = Role.objects.get(role_id=role_id)
            except Role.DoesNotExist:
                return Response({
                    'error': 'ROLE_NOT_FOUND',
                    'message': f'Role with ID {role_id} does not exist'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Create user
            with transaction.atomic():
                user = User(
                    first_name=request.data.get('first_name'),
                    last_name=request.data.get('last_name'),
                    company_email=email,
                    email=email,
                    cmp=request.user.cmp,
                    roles=role,
                    company_name=request.user.cmp.data,  # Use company name from admin's company
                    employee_range=request.user.employee_range,
                    country=request.user.country,
                    is_active=True,
                )
                user.set_password(request.data.get('password'))
                user.save()
            
            if settings.DEBUG:
                print(f"✅ [UserManagementAPIView.post] Created user {user.user_id} with role {role.role_name}")
            
            return Response({
                'message': 'User created successfully',
                'user': {
                    'user_id': user.user_id,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'company_email': user.company_email,
                    'role': role.role_name,
                    'role_id': role.role_id,
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            if settings.DEBUG:
                print(f"❌ [UserManagementAPIView.post] Error: {e}")
                import traceback
                traceback.print_exc()
            return Response({
                'error': 'INTERNAL_ERROR',
                'message': 'Failed to create user'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def patch(self, request, user_id=None):
        """Update user role."""
        try:
            if not user_id:
                return Response({
                    'error': 'VALIDATION_ERROR',
                    'message': 'User ID is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get user
            try:
                user = User.objects.get(user_id=user_id, cmp=request.user.cmp)
            except User.DoesNotExist:
                return Response({
                    'error': 'USER_NOT_FOUND',
                    'message': f'User with ID {user_id} not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Prevent changing own role
            if user.user_id == request.user.user_id:
                return Response({
                    'error': 'FORBIDDEN',
                    'message': 'You cannot change your own role'
                }, status=status.HTTP_403_FORBIDDEN)
            
            # Get new role
            role_id = request.data.get('role_id')
            if not role_id:
                return Response({
                    'error': 'VALIDATION_ERROR',
                    'message': 'role_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                role = Role.objects.get(role_id=role_id)
            except Role.DoesNotExist:
                return Response({
                    'error': 'ROLE_NOT_FOUND',
                    'message': f'Role with ID {role_id} does not exist'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Update role
            user.roles = role
            user.save()
            
            if settings.DEBUG:
                print(f"✅ [UserManagementAPIView.patch] Updated user {user_id} role to {role.role_name}")
            
            return Response({
                'message': 'User role updated successfully',
                'user': {
                    'user_id': user.user_id,
                    'role': role.role_name,
                    'role_id': role.role_id,
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            if settings.DEBUG:
                print(f"❌ [UserManagementAPIView.patch] Error: {e}")
                import traceback
                traceback.print_exc()
            return Response({
                'error': 'INTERNAL_ERROR',
                'message': 'Failed to update user'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RoleListAPIView(APIView):
    """
    Get list of available roles.
    Accessible to administrators only.
    """
    
    permission_classes = [IsAuthenticated, IsAdministrator]
    
    def get(self, request):
        """Get all available roles."""
        try:
            roles = Role.objects.all().order_by('role_id')
            
            role_data = [
                {
                    'role_id': role.role_id,
                    'role_name': role.role_name
                }
                for role in roles
            ]
            
            return Response({
                'roles': role_data
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            if settings.DEBUG:
                print(f"❌ [RoleListAPIView.get] Error: {e}")
            return Response({
                'error': 'INTERNAL_ERROR',
                'message': 'Failed to retrieve roles'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
