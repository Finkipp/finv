import random
import string
from django.db import models
from django.contrib.auth.models import User


class EquipmentType(models.Model):
    name = models.CharField("Наименование", max_length=255, unique=True)

    class Meta:
        verbose_name = "Тип оборудования/расходника"
        verbose_name_plural = "Типы оборудования/расходников"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Location(models.Model):
    name = models.CharField("Наименование", max_length=255, unique=True)

    class Meta:
        verbose_name = "Местоположение"
        verbose_name_plural = "Местоположения"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField("Наименование", max_length=255, unique=True)

    class Meta:
        verbose_name = "Поставщик"
        verbose_name_plural = "Поставщики"
        ordering = ["name"]

    def __str__(self):
        return self.name


class DisposalDirection(models.Model):
    name = models.CharField("Наименование", max_length=255, unique=True)

    class Meta:
        verbose_name = "Направление ухода"
        verbose_name_plural = "Направления ухода"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Operator(models.Model):
    full_name = models.CharField("ФИО", max_length=255, unique=True)

    class Meta:
        verbose_name = "Эксплуатант"
        verbose_name_plural = "Эксплуатанты"
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class Equipment(models.Model):
    STATUS_CHOICES = [
        ("working", "Рабочий"),
        ("broken", "Нерабочий"),
        ("unknown", "Неизвестно"),
        ("repair", "Ремонт"),
    ]

    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, verbose_name="Тип оборудования"
    )
    name = models.CharField("Наименование", max_length=255)
    description = models.TextField("Описание", blank=True)
    inventory_number = models.CharField(
        "Инвентарный номер", max_length=20, unique=True
    )
    status = models.CharField(
        "Статус", max_length=20, choices=STATUS_CHOICES, default="unknown"
    )
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Местоположение"
    )
    operator = models.ForeignKey(
        Operator, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Эксплуатация"
    )
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Оборудование"
        verbose_name_plural = "Оборудование"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.inventory_number})"

    @staticmethod
    def generate_inventory_number():
        while True:
            num = random.randint(1000000, 9999999)
            inv_num = f"iin{num}"
            if not Equipment.objects.filter(inventory_number=inv_num).exists():
                return inv_num


class Consumable(models.Model):
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, verbose_name="Тип расходника"
    )
    name = models.CharField("Наименование", max_length=255)
    quantity = models.PositiveIntegerField("Количество", default=0)
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Местоположение"
    )
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Расходник"
        verbose_name_plural = "Расходники"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.quantity})"


class Receipt(models.Model):
    STATUS_CHOICES = [
        ("new", "Новое"),
        ("used", "Б/У"),
    ]

    date = models.DateField("Дата поступления", auto_now_add=True)
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, verbose_name="Тип расходника"
    )
    name = models.CharField("Наименование", max_length=255)
    status = models.CharField("Статус", max_length=10, choices=STATUS_CHOICES, default="new")
    quantity = models.PositiveIntegerField("Количество")
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Поставщик"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Поступление"
        verbose_name_plural = "Поступления"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.name} от {self.date}"


class Disposal(models.Model):
    date = models.DateField("Дата ухода", auto_now_add=True)
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, verbose_name="Тип расходника"
    )
    name = models.CharField("Наименование", max_length=255)
    quantity = models.PositiveIntegerField("Количество")
    direction = models.ForeignKey(
        DisposalDirection, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="Направление ухода"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Уход"
        verbose_name_plural = "Уходы"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.name} от {self.date}"


class WriteOff(models.Model):
    OBJECT_TYPE_CHOICES = [
        ("equipment", "Оборудование"),
        ("consumable", "Расходник"),
    ]
    STATUS_CHOICES = [
        ("in_progress", "В процессе"),
        ("completed", "Завершено"),
    ]

    object_type = models.CharField(
        "Тип объекта", max_length=15, choices=OBJECT_TYPE_CHOICES
    )
    name = models.CharField("Наименование", max_length=255)
    reason = models.TextField("Причина списания", blank=True)
    actual_date = models.DateField("Дата фактического списания", auto_now_add=True)
    final_date = models.DateField(
        "Дата окончательного списания", null=True, blank=True
    )
    status = models.CharField(
        "Статус", max_length=15, choices=STATUS_CHOICES, default="in_progress"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Списание"
        verbose_name_plural = "Списания"
        ordering = ["-actual_date"]

    def __str__(self):
        return f"{self.name} ({self.get_object_type_display()})"
