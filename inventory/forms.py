from django import forms
from .models import (
    Equipment, Consumable, Receipt, Disposal, WriteOff, Order, Movement,
    EquipmentType, Location, Supplier, DisposalDirection, Operator,
)


class EquipmentForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = [
            "equipment_type", "name", "description", "inventory_number",
            "status", "location", "operator",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "status": forms.Select(choices=Equipment.STATUS_CHOICES),
        }


class EquipmentSearchForm(forms.Form):
    q = forms.CharField(
        label="Поиск", required=False,
        widget=forms.TextInput(attrs={"placeholder": "Поиск по номеру, наименованию, помещению..."}),
    )


class ConsumableForm(forms.ModelForm):
    class Meta:
        model = Consumable
        fields = ["equipment_type", "name", "quantity", "location"]


class ReceiptForm(forms.ModelForm):
    class Meta:
        model = Receipt
        fields = ["equipment_type", "name", "status", "quantity", "supplier"]
        widgets = {
            "status": forms.Select(choices=Receipt.STATUS_CHOICES),
        }


class DisposalForm(forms.ModelForm):
    link_equipment = forms.ModelChoiceField(
        queryset=Equipment.objects.all(),
        required=False,
        label="Оборудование (выберите существующее)",
    )
    link_consumable = forms.ModelChoiceField(
        queryset=Consumable.objects.all(),
        required=False,
        label="Расходник (выберите существующий)",
    )

    class Meta:
        model = Disposal
        fields = ["equipment_type", "name", "quantity", "direction", "link_equipment", "link_consumable"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("link_equipment") and cleaned.get("link_consumable"):
            raise forms.ValidationError("Выберите только одно: оборудование ИЛИ расходник")
        return cleaned


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["branch", "equipment_type", "name", "quantity", "status", "note"]
        widgets = {
            "status": forms.Select(choices=Order.STATUS_CHOICES),
            "note": forms.Textarea(attrs={"rows": 3}),
        }


class MovementForm(forms.ModelForm):
    class Meta:
        model = Movement
        fields = ["equipment", "consumable", "from_location", "to_location", "from_operator", "to_operator", "note"]
        widgets = {
            "note": forms.Textarea(attrs={"rows": 3}),
        }


class WriteOffForm(forms.ModelForm):
    class Meta:
        model = WriteOff
        fields = ["object_type", "name", "reason", "final_date", "status"]
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 4}),
            "object_type": forms.Select(choices=WriteOff.OBJECT_TYPE_CHOICES),
            "status": forms.Select(choices=WriteOff.STATUS_CHOICES),
            "final_date": forms.DateInput(attrs={"type": "date"}),
        }
