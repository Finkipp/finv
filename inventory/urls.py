from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.user_login, name="login"),
    path("logout/", views.user_logout, name="logout"),
    # Equipment
    path("equipment/", views.EquipmentListView.as_view(), name="equipment_list"),
    path("equipment/add/", views.EquipmentCreateView.as_view(), name="equipment_add"),
    path("equipment/<int:pk>/", views.EquipmentDetailView.as_view(), name="equipment_detail"),
    path("equipment/<int:pk>/edit/", views.EquipmentUpdateView.as_view(), name="equipment_edit"),
    path("equipment/<int:pk>/delete/", views.EquipmentDeleteView.as_view(), name="equipment_delete"),
    path("equipment/generate-inv/", views.generate_inventory_number, name="generate_inv"),
    # Consumables
    path("consumables/", views.ConsumableListView.as_view(), name="consumable_list"),
    path("consumables/add/", views.ConsumableCreateView.as_view(), name="consumable_add"),
    path("consumables/<int:pk>/edit/", views.ConsumableUpdateView.as_view(), name="consumable_edit"),
    path("consumables/<int:pk>/delete/", views.ConsumableDeleteView.as_view(), name="consumable_delete"),
    # Receipts
    path("receipts/", views.ReceiptListView.as_view(), name="receipt_list"),
    path("receipts/add/", views.ReceiptCreateView.as_view(), name="receipt_add"),
    path("receipts/<int:pk>/edit/", views.ReceiptUpdateView.as_view(), name="receipt_edit"),
    path("receipts/<int:pk>/delete/", views.ReceiptDeleteView.as_view(), name="receipt_delete"),
    # Disposals
    path("disposals/", views.DisposalListView.as_view(), name="disposal_list"),
    path("disposals/add/", views.DisposalCreateView.as_view(), name="disposal_add"),
    path("disposals/<int:pk>/edit/", views.DisposalUpdateView.as_view(), name="disposal_edit"),
    path("disposals/<int:pk>/delete/", views.DisposalDeleteView.as_view(), name="disposal_delete"),
    # Write-offs
    path("writeoffs/", views.WriteOffListView.as_view(), name="writeoff_list"),
    path("writeoffs/add/", views.WriteOffCreateView.as_view(), name="writeoff_add"),
    path("writeoffs/<int:pk>/edit/", views.WriteOffUpdateView.as_view(), name="writeoff_edit"),
    path("writeoffs/<int:pk>/delete/", views.WriteOffDeleteView.as_view(), name="writeoff_delete"),
    # References
    path("refs/equipment-types/", views.EquipmentTypeListView.as_view(), name="eqtype_list"),
    path("refs/equipment-types/add/", views.EquipmentTypeCreateView.as_view(), name="eqtype_add"),
    path("refs/equipment-types/<int:pk>/edit/", views.EquipmentTypeUpdateView.as_view(), name="eqtype_edit"),
    path("refs/equipment-types/<int:pk>/delete/", views.EquipmentTypeDeleteView.as_view(), name="eqtype_delete"),
    path("refs/locations/", views.LocationListView.as_view(), name="location_list"),
    path("refs/locations/add/", views.LocationCreateView.as_view(), name="location_add"),
    path("refs/locations/<int:pk>/edit/", views.LocationUpdateView.as_view(), name="location_edit"),
    path("refs/locations/<int:pk>/delete/", views.LocationDeleteView.as_view(), name="location_delete"),
    path("refs/suppliers/", views.SupplierListView.as_view(), name="supplier_list"),
    path("refs/suppliers/add/", views.SupplierCreateView.as_view(), name="supplier_add"),
    path("refs/suppliers/<int:pk>/edit/", views.SupplierUpdateView.as_view(), name="supplier_edit"),
    path("refs/suppliers/<int:pk>/delete/", views.SupplierDeleteView.as_view(), name="supplier_delete"),
    path("refs/directions/", views.DisposalDirectionListView.as_view(), name="direction_list"),
    path("refs/directions/add/", views.DisposalDirectionCreateView.as_view(), name="direction_add"),
    path("refs/directions/<int:pk>/edit/", views.DisposalDirectionUpdateView.as_view(), name="direction_edit"),
    path("refs/directions/<int:pk>/delete/", views.DisposalDirectionDeleteView.as_view(), name="direction_delete"),
    path("refs/operators/", views.OperatorListView.as_view(), name="operator_list"),
    path("refs/operators/add/", views.OperatorCreateView.as_view(), name="operator_add"),
    path("refs/operators/<int:pk>/edit/", views.OperatorUpdateView.as_view(), name="operator_edit"),
    path("refs/operators/<int:pk>/delete/", views.OperatorDeleteView.as_view(), name="operator_delete"),
]
