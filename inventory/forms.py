from django import forms
from django.contrib.auth import get_user_model

from .models import (
    Equipment, Consumable, Receipt, Disposal, WriteOff, Order,
    EquipmentType, Location, Supplier, DisposalDirection, Operator, UserProfile,
    consumable_name_hash,
)


class EquipmentForm(forms.ModelForm):
    inventory_number = forms.CharField(
        required=False,
        label="Инвентарный номер",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Оставьте пустым для автогенерации",
            }
        ),
    )
    change_note = forms.CharField(
        required=False,
        label="Комментарий к изменению",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 2,
                "placeholder": "Например: передано в сервисный центр",
            }
        ),
    )

    class Meta:
        model = Equipment
        fields = [
            "equipment_type", "name", "description", "inventory_number",
            "status", "location", "operator",
        ]
        widgets = {
            "equipment_type": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Например: Ноутбук Dell Latitude 5490"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "inventory_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "Оставьте пустым для автогенерации"}),
            "status": forms.Select(attrs={"class": "form-control"}, choices=Equipment.STATUS_CHOICES),
            "location": forms.Select(attrs={"class": "form-control"}),
            "operator": forms.Select(attrs={"class": "form-control"}),
        }

    def clean_inventory_number(self):
        value = self.cleaned_data["inventory_number"].strip()
        return value or Equipment.generate_inventory_number()


class EquipmentSearchForm(forms.Form):
    q = forms.CharField(
        label="Поиск", required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Поиск по номеру, наименованию, помещению..."}),
    )


class ConsumableForm(forms.ModelForm):
    class Meta:
        model = Consumable
        fields = ["equipment_type", "name", "quantity", "status", "location"]
        widgets = {
            "equipment_type": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Наименование расходника"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "placeholder": "0"}),
            "status": forms.Select(attrs={"class": "form-control"}, choices=Consumable.STATUS_CHOICES),
            "location": forms.Select(attrs={"class": "form-control"}),
        }


class ReceiptForm(forms.ModelForm):
    CREATE_TARGET_CHOICES = [
        ("none", "Только поступление"),
        ("equipment", "Оборудование"),
        ("consumable", "Расходники"),
    ]

    create_target = forms.ChoiceField(
        choices=CREATE_TARGET_CHOICES, initial="none",
        label="Добавить запись в таблицу",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    eq_inventory_number = forms.CharField(
        required=False, label="Инвентарный номер",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Оставьте пустым для автогенерации"}),
    )
    eq_description = forms.CharField(
        required=False, label="Описание",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Характеристики, примечания..."}),
    )
    eq_location = forms.ModelChoiceField(
        queryset=Location.objects.all(), required=False,
        label="Местоположение",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    eq_operator = forms.ModelChoiceField(
        queryset=Operator.objects.all(), required=False,
        label="Эксплуатант",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    eq_status = forms.ChoiceField(
        choices=Equipment.STATUS_CHOICES, initial="unknown", required=False,
        label="Статус оборудования",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    con_location = forms.ModelChoiceField(
        queryset=Location.objects.all(), required=False,
        label="Местоположение",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    con_status = forms.ChoiceField(
        choices=Consumable.STATUS_CHOICES, initial="unknown", required=False,
        label="Статус расходника",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Receipt
        fields = ["date", "equipment_type", "name", "status", "quantity", "supplier"]
        widgets = {
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "equipment_type": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Наименование поступления"}),
            "status": forms.Select(attrs={"class": "form-control"}, choices=Receipt.STATUS_CHOICES),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "placeholder": "1"}),
            "supplier": forms.Select(attrs={"class": "form-control"}),
        }

    def clean(self):
        cleaned = super().clean()
        target = cleaned.get("create_target")
        if target == "equipment":
            if cleaned.get("quantity") != 1:
                self.add_error(
                    "quantity",
                    "Для оборудования укажите 1: каждой единице нужен отдельный инвентарный номер.",
                )
            inventory_number = cleaned.get("eq_inventory_number", "").strip()
            max_length = Equipment._meta.get_field("inventory_number").max_length
            if len(inventory_number) > max_length:
                self.add_error("eq_inventory_number", "Инвентарный номер слишком длинный.")
            if inventory_number and Equipment.objects.filter(
                inventory_number=inventory_number
            ).exists():
                self.add_error(
                    "eq_inventory_number", "Такой инвентарный номер уже существует."
                )
        elif target == "consumable":
            if not cleaned.get("con_status"):
                cleaned["con_status"] = "unknown"
        return cleaned


class DisposalForm(forms.ModelForm):
    CREATE_TARGET_CHOICES = [
        ("none", "Только уход"),
        ("equipment", "Оборудование"),
        ("consumable", "Расходники"),
    ]

    create_target = forms.ChoiceField(
        choices=CREATE_TARGET_CHOICES, initial="none",
        label="Привязать к существующей записи",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Disposal
        fields = ["date", "equipment_type", "name", "quantity", "direction"]
        widgets = {
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "equipment_type": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Наименование"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "placeholder": "1"}),
            "direction": forms.Select(attrs={"class": "form-control"}),
        }

    def clean(self):
        cleaned = super().clean()
        target = cleaned.get("create_target", "none")
        if target == "equipment":
            name = cleaned.get("name", "")
            eq_type = cleaned.get("equipment_type")
            if name and eq_type:
                matches = Equipment.objects.filter(
                    equipment_type=eq_type, name__iexact=name
                )
                if not matches.exists():
                    raise forms.ValidationError(
                        f"Оборудование «{name}» с таким типом не найдено. "
                        "Сначала добавьте его в таблицу Оборудования."
                    )
                if matches.count() > 1:
                    raise forms.ValidationError(
                        "Найдено несколько единиц оборудования с таким названием. "
                        "Измените нужную единицу непосредственно в разделе «Оборудование»."
                    )
                if cleaned.get("quantity") != 1:
                    self.add_error(
                        "quantity", "Для единицы оборудования укажите количество 1."
                    )
        elif target == "consumable":
            name = cleaned.get("name", "")
            eq_type = cleaned.get("equipment_type")
            if name and eq_type:
                matches = Consumable.objects.filter(
                    equipment_type=eq_type,
                    normalized_name_hash=consumable_name_hash(name),
                )
                if not matches.exists():
                    raise forms.ValidationError(
                        f"Расходник «{name}» с таким типом не найден. "
                        "Сначала добавьте его в таблицу Расходников."
                    )
                if matches.count() > 1:
                    raise forms.ValidationError(
                        "Найдено несколько позиций расходника. Измените нужную позицию "
                        "непосредственно в разделе «Расходники»."
                    )
                quantity = cleaned.get("quantity") or 0
                available = matches.values_list("quantity", flat=True).first()
                if available < quantity:
                    self.add_error(
                        "quantity", f"Недостаточно на складе. Доступно: {available}."
                    )
        return cleaned


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["branch", "equipment_type", "name", "quantity", "status", "note"]
        widgets = {
            "branch": forms.TextInput(attrs={"class": "form-control", "placeholder": "Филиал или отдел"}),
            "equipment_type": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Наименование"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "placeholder": "1"}),
            "status": forms.Select(attrs={"class": "form-control"}, choices=Order.STATUS_CHOICES),
            "note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class WriteOffForm(forms.ModelForm):
    class Meta:
        model = WriteOff
        fields = ["object_type", "name", "reason", "final_date", "status"]
        widgets = {
            "object_type": forms.Select(attrs={"class": "form-control"}, choices=WriteOff.OBJECT_TYPE_CHOICES),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Наименование объекта списания"}),
            "reason": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "Укажите причину списания..."}),
            "status": forms.Select(attrs={"class": "form-control"}, choices=WriteOff.STATUS_CHOICES),
            "final_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == "completed" and not cleaned.get("final_date"):
            self.add_error("final_date", "Укажите дату завершённого списания.")
        return cleaned


class ProfileUserForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["avatar", "notification_level"]
        widgets = {
            "avatar": forms.ClearableFileInput(
                attrs={"class": "form-control", "accept": "image/*"}
            ),
            "notification_level": forms.Select(attrs={"class": "form-control"}),
        }

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and getattr(avatar, "size", 0) > 2 * 1024 * 1024:
            raise forms.ValidationError("Размер изображения не должен превышать 2 МБ.")
        return avatar
