"""
URL configuration for Elttool project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path,include




# from .views import google_login,google_signup,SignUpView,SignInView
from django.views.generic import RedirectView
from django.conf.urls.static import static
from django.conf import settings
from apps.elttool.v1.views import stripe_webhook



urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/webhook/", stripe_webhook),
    
    # path("",include("apps.elttool.v1.urls")),
    path('accounts/signup/', RedirectView.as_view(pattern_name='signup')),
    path('accounts/login/', RedirectView.as_view(pattern_name='signin')),
    
    path("accounts/",include("allauth.urls")),
    

    # path("login/",SignInView.as_view(),name="signin"),
    # path("signup/", SignUpView.as_view(), name="signup"),
    
    # path('google/login/',google_login, name='google_login'),
    # path('google/signup/', google_signup, name='google_signup'),

    path('api/v1/', include('apps.elttool.v1.urls')),
    path('api/v1/support-admin/', include('apps.support_admin.urls')),

    
    
   
    
]+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
