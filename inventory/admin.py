from django.contrib import admin
from django.db import transaction
from .models import (
    EquipmentType, Location, Supplier, DisposalDirection, Operator,
    Equipment, Consumable, Receipt, Disposal, WriteOff, Order, Movement,
    AuditLog, Notification, UserProfile,
)


class LockedAuditAdmin:
    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            if change:
                type(obj).objects.select_for_update().get(pk=obj.pk)
            super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        with transaction.atomic():
            type(obj).objects.select_for_update().get(pk=obj.pk)
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with transaction.atomic():
            list(queryset.select_for_update().values_list("pk", flat=True))
            super().delete_queryset(request, queryset)


@admin.register(EquipmentType)
class EquipmentTypeAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Location)
class LocationAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Supplier)
class SupplierAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(DisposalDirection)
class DisposalDirectionAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Operator)
class OperatorAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("full_name",)
    search_fields = ("full_name",)


@admin.register(Equipment)
class EquipmentAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name", "inventory_number", "equipment_type", "status", "location", "operator")
    list_filter = ("status", "equipment_type", "location")
    search_fields = ("name", "inventory_number", "description")


@admin.register(Consumable)
class ConsumableAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name", "equipment_type", "quantity", "status", "location")
    list_filter = ("status", "equipment_type", "location")
    search_fields = ("name",)


@admin.register(Receipt)
class ReceiptAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name", "date", "equipment_type", "status", "quantity", "supplier")
    list_filter = ("status", "equipment_type", "supplier")
    search_fields = ("name",)


@admin.register(Disposal)
class DisposalAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name", "date", "equipment_type", "quantity", "direction")
    list_filter = ("equipment_type", "direction")
    search_fields = ("name",)


@admin.register(WriteOff)
class WriteOffAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name", "object_type", "status", "actual_date", "final_date")
    list_filter = ("status", "object_type")
    search_fields = ("name", "reason")


@admin.register(Order)
class OrderAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("name", "branch", "equipment_type", "quantity", "status", "created_at")
    list_filter = ("status", "equipment_type")
    search_fields = ("name", "branch", "note")


@admin.register(Movement)
class MovementAdmin(admin.ModelAdmin):
    list_display = ("equipment", "consumable", "from_location", "to_location", "created_at")
    list_filter = ("from_location", "to_location")
    search_fields = ("note",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at", "action", "object_type_label", "object_repr", "actor",
    )
    list_filter = ("action", "object_type", "created_at")
    search_fields = ("object_repr", "object_type_label", "note", "actor__username")
    readonly_fields = (
        "action", "object_type", "object_type_label", "object_id", "object_repr",
        "changes", "note", "actor", "actor_username", "actor_display_name",
        "ip_address", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserProfile)
class UserProfileAdmin(LockedAuditAdmin, admin.ModelAdmin):
    list_display = ("user", "notification_level")
    list_filter = ("notification_level",)
    search_fields = ("user__username", "user__first_name", "user__last_name")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "audit_log", "is_read", "created_at")
    list_filter = ("is_read", "created_at")
    search_fields = ("user__username", "audit_log__object_repr")
    readonly_fields = ("user", "audit_log", "is_read", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
