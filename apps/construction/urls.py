from django.urls import re_path

from apps.construction import views

urlpatterns = [
    re_path(r'^config/$', views.ConfigView.as_view())
]
