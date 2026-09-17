from django.contrib import admin
from django.urls import path, include
from inventory import views as inv_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("inventory.urls")),
]
