import jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import AdminUser
from .auth import decode_admin_token


class AdminJWTAuthentication(BaseAuthentication):
    """
    Custom authentication class for support admin.
    Reads Bearer token, decodes it, and returns the AdminUser.
    Completely separate from the main app's JWT authentication.
    """

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return None

        token = auth_header.split(' ')[1]
        try:
            payload = decode_admin_token(token)
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Admin token has expired.')
        except jwt.InvalidTokenError:
            raise AuthenticationFailed('Invalid admin token.')

        if payload.get('type') != 'access':
            raise AuthenticationFailed('Invalid token type.')

        try:
            admin = AdminUser.objects.get(admin_id=payload['admin_id'], is_active=True)
        except AdminUser.DoesNotExist:
            raise AuthenticationFailed('Admin user not found or inactive.')

        return (admin, token)
