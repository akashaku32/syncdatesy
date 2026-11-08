import jwt
import datetime
from django.conf import settings

ADMIN_JWT_SECRET = getattr(settings, 'ADMIN_JWT_SECRET', settings.SECRET_KEY)
ADMIN_JWT_ALGORITHM = 'HS256'
ADMIN_ACCESS_TOKEN_LIFETIME_MINUTES = 60
ADMIN_REFRESH_TOKEN_LIFETIME_DAYS = 7
ADMIN_RESET_PASSWORD_TOKEN_LIFETIME_MINUTES = 60


def generate_admin_tokens(admin_user):
    now = datetime.datetime.utcnow()

    access_payload = {
        'admin_id': admin_user.admin_id,
        'email': admin_user.email,
        'type': 'access',
        'exp': now + datetime.timedelta(minutes=ADMIN_ACCESS_TOKEN_LIFETIME_MINUTES),
        'iat': now,
    }
    refresh_payload = {
        'admin_id': admin_user.admin_id,
        'email': admin_user.email,
        'type': 'refresh',
        'exp': now + datetime.timedelta(days=ADMIN_REFRESH_TOKEN_LIFETIME_DAYS),
        'iat': now,
    }

    access_token = jwt.encode(access_payload, ADMIN_JWT_SECRET, algorithm=ADMIN_JWT_ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, ADMIN_JWT_SECRET, algorithm=ADMIN_JWT_ALGORITHM)

    return access_token, refresh_token


def generate_admin_reset_token(admin_user):
    now = datetime.datetime.utcnow()
    payload = {
        'admin_id': admin_user.admin_id,
        'email': admin_user.email,
        'type': 'reset',
        'exp': now + datetime.timedelta(minutes=ADMIN_RESET_PASSWORD_TOKEN_LIFETIME_MINUTES),
        'iat': now,
    }
    return jwt.encode(payload, ADMIN_JWT_SECRET, algorithm=ADMIN_JWT_ALGORITHM)


def decode_admin_token(token):
    return jwt.decode(token, ADMIN_JWT_SECRET, algorithms=[ADMIN_JWT_ALGORITHM])
