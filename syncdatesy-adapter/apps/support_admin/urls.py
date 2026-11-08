from django.urls import path
from .views import (
    AdminLoginView,
    AdminRegisterView,
    AdminForgotPasswordView,
    AdminResetPasswordView,
    AdminTokenRefreshView,
    AdminMeView,
    AdminRegistrationRequestListView,
    AdminRegistrationRequestActionView,
    AdminRegistrationRequestExtendEndDateView,
    AdminCompanyListView,
    AdminCompanyConnectionsView,
    AdminSelectedTablesView,
    AdminAddOffsetHoursView,
    AdminSubtractOffsetHoursView,
    AdminCompanyBillingView,
    AdminCompanyInvoicesView,
    AdminCompanyBillingSummaryView,
    AdminCompanyDestinationListView,
    AdminCompanyDestinationDetailView,
    AdminCompanyDestinationTestView,
    AdminCompanyConnectionTestView,
    AdminTicketListView,
    AdminTicketDetailView,
    AdminTicketRepliesView,
)

urlpatterns = [
    # Auth
    path('auth/register/', AdminRegisterView.as_view(), name='admin-register'),
    path('auth/login/', AdminLoginView.as_view(), name='admin-login'),
    path('auth/forgot-password/', AdminForgotPasswordView.as_view(), name='admin-forgot-password'),
    path('auth/reset-password/', AdminResetPasswordView.as_view(), name='admin-reset-password'),
    path('auth/refresh/', AdminTokenRefreshView.as_view(), name='admin-token-refresh'),
    path('auth/me/', AdminMeView.as_view(), name='admin-me'),

    # Registration requests
    path(
        'registration-requests/',
        AdminRegistrationRequestListView.as_view(),
        name='admin-registration-request-list',
    ),
    path(
        'registration-requests/<int:registration_request_id>/',
        AdminRegistrationRequestActionView.as_view(),
        name='admin-registration-request-action',
    ),
   path(
    'registration-requests/<int:registration_request_id>/extend-end-date/',
    AdminRegistrationRequestExtendEndDateView.as_view(),
    name='admin-registration-request-extend-end-date',
),

    # Companies
    path('companies/', AdminCompanyListView.as_view(), name='admin-company-list'),
    path('companies/<int:cmp_id>/connections/', AdminCompanyConnectionsView.as_view(), name='admin-company-connections'),
    path('companies/<int:cmp_id>/connections/<int:connection_id>/test/', AdminCompanyConnectionTestView.as_view(), name='admin-company-connection-test'),
    path('companies/<int:cmp_id>/connections/<int:connection_id>/selected-tables/', AdminSelectedTablesView.as_view(), name='admin-company-connection-selected-tables'),
    path('companies/<int:cmp_id>/connections/<int:connection_id>/subtract-offset-hours/', AdminSubtractOffsetHoursView.as_view(), name='subtract-offset-hours'),
    path('companies/<int:cmp_id>/connections/<int:connection_id>/add-offset-hours/', AdminAddOffsetHoursView.as_view(), name='add-offset-hours'),
    path('companies/<int:cmp_id>/billing/', AdminCompanyBillingView.as_view(), name='admin-company-billing'),
    path('companies/<int:cmp_id>/invoices/', AdminCompanyInvoicesView.as_view(), name='admin-company-invoices'),
    path('companies/<int:cmp_id>/billing-summary/', AdminCompanyBillingSummaryView.as_view(), name='admin-company-billing-summary'),
    path('companies/<int:cmp_id>/destinations/', AdminCompanyDestinationListView.as_view(), name='admin-company-destination-list'),
    path('companies/<int:cmp_id>/destinations/<int:dst_config_id>/', AdminCompanyDestinationDetailView.as_view(), name='admin-company-destination-detail'),
    path('companies/<int:cmp_id>/destinations/<int:dst_config_id>/test/', AdminCompanyDestinationTestView.as_view(), name='admin-company-destination-test'),
    # Tickets
    path('tickets/', AdminTicketListView.as_view(), name='admin-ticket-list'),
    path('tickets/<int:ticket_id>/', AdminTicketDetailView.as_view(), name='admin-ticket-detail'),
    path('tickets/<int:ticket_id>/replies/', AdminTicketRepliesView.as_view(), name='admin-ticket-replies'),
]
