from django import forms
from .models import (
    Equipment, Consumable, Receipt, Disposal, WriteOff,
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
    class Meta:
        model = Disposal
        fields = ["equipment_type", "name", "quantity", "direction"]


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
