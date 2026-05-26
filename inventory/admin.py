from django.contrib import admin
from .models import (
    EquipmentType, Location, Supplier, DisposalDirection, Operator,
    Equipment, Consumable, Receipt, Disposal, WriteOff, Order, Movement,
)


@admin.register(EquipmentType)
class EquipmentTypeAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(DisposalDirection)
class DisposalDirectionAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = ("full_name",)
    search_fields = ("full_name",)


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "inventory_number", "equipment_type", "status", "location", "operator")
    list_filter = ("status", "equipment_type", "location")
    search_fields = ("name", "inventory_number", "description")


@admin.register(Consumable)
class ConsumableAdmin(admin.ModelAdmin):
    list_display = ("name", "equipment_type", "quantity", "location")
    list_filter = ("equipment_type", "location")
    search_fields = ("name",)


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("name", "date", "equipment_type", "status", "quantity", "supplier")
    list_filter = ("status", "equipment_type", "supplier")
    search_fields = ("name",)


@admin.register(Disposal)
class DisposalAdmin(admin.ModelAdmin):
    list_display = ("name", "date", "equipment_type", "quantity", "direction")
    list_filter = ("equipment_type", "direction")
    search_fields = ("name",)


@admin.register(WriteOff)
class WriteOffAdmin(admin.ModelAdmin):
    list_display = ("name", "object_type", "status", "actual_date", "final_date")
    list_filter = ("status", "object_type")
    search_fields = ("name", "reason")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "equipment_type", "quantity", "status", "created_at")
    list_filter = ("status", "equipment_type")
    search_fields = ("name", "branch", "note")


@admin.register(Movement)
class MovementAdmin(admin.ModelAdmin):
    list_display = ("equipment", "consumable", "from_location", "to_location", "created_at")
    list_filter = ("from_location", "to_location")
    search_fields = ("note",)
