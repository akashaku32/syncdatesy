from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status


def custom_exception_handler(exc, context):
    """
    Custom exception handler that adds redirect info for trial expiration.
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)
    
    # Check if request has trial_expired_redirect (set by CheckTrialPeriod)
    request = context.get('request')
    if response and request and hasattr(request, 'trial_expired_redirect'):
        response.data = {
            'error': 'Your trial period has expired. Please select a subscription plan.',
            'message': 'Your trial period has expired. Please select a subscription plan.',
            'redirect_to': request.trial_expired_redirect,
            'trial_expired': True
        }
        response.status_code = status.HTTP_403_FORBIDDEN
    
    return response

