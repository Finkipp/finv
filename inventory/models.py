import hashlib
import random
import unicodedata
from datetime import date

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def normalize_consumable_name(value):
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def consumable_name_hash(value):
    normalized = normalize_consumable_name(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
        Location, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Местоположение"
    )
    operator = models.ForeignKey(
        Operator, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Эксплуатация"
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
    STATUS_CHOICES = [
        ("working", "Рабочий"),
        ("broken", "Нерабочий"),
        ("unknown", "Неизвестно"),
        ("repair", "Ремонт"),
    ]

    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, verbose_name="Тип расходника"
    )
    name = models.CharField("Наименование", max_length=255)
    normalized_name_hash = models.CharField(
        max_length=64, editable=False, blank=True, default=""
    )
    quantity = models.PositiveIntegerField("Количество", default=0)
    status = models.CharField(
        "Статус", max_length=20, choices=STATUS_CHOICES, default="unknown"
    )
    location = models.ForeignKey(
        Location, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Местоположение"
    )
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Расходник"
        verbose_name_plural = "Расходники"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_name_hash", "equipment_type", "location"],
                condition=models.Q(location__isnull=False),
                name="unique_consumable_position_with_location",
            ),
            models.UniqueConstraint(
                fields=["normalized_name_hash", "equipment_type"],
                condition=models.Q(location__isnull=True),
                name="unique_consumable_position_without_location",
            ),
        ]

    def clean(self):
        super().clean()
        self.normalized_name_hash = consumable_name_hash(self.name)

    def save(self, *args, **kwargs):
        self.normalized_name_hash = consumable_name_hash(self.name)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.quantity})"


class Receipt(models.Model):
    STATUS_CHOICES = [
        ("new", "Новое"),
        ("used", "Б/У"),
    ]

    date = models.DateField("Дата поступления", default=date.today)
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, verbose_name="Тип расходника"
    )
    name = models.CharField("Наименование", max_length=255)
    status = models.CharField("Статус", max_length=10, choices=STATUS_CHOICES, default="new")
    quantity = models.PositiveIntegerField(
        "Количество", validators=[MinValueValidator(1)]
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Поставщик"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Поступление"
        verbose_name_plural = "Поступления"
        ordering = ["-date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="receipt_quantity_gte_1",
            ),
        ]

    def __str__(self):
        return f"{self.name} от {self.date}"


class Disposal(models.Model):
    date = models.DateField("Дата ухода", default=date.today)
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, verbose_name="Тип расходника"
    )
    name = models.CharField("Наименование", max_length=255)
    quantity = models.PositiveIntegerField(
        "Количество", validators=[MinValueValidator(1)]
    )
    direction = models.ForeignKey(
        DisposalDirection, on_delete=models.PROTECT, null=True, blank=True,
        verbose_name="Направление ухода"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Уход"
        verbose_name_plural = "Уходы"
        ordering = ["-date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="disposal_quantity_gte_1",
            ),
        ]

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


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Ожидает"),
        ("ordered", "Заказано"),
        ("fulfilled", "Выполнен"),
        ("cancelled", "Отменён"),
    ]
    branch = models.CharField("Филиал/Отдел", max_length=255)
    equipment_type = models.ForeignKey(EquipmentType, on_delete=models.PROTECT, verbose_name="Тип")
    name = models.CharField("Наименование", max_length=255)
    quantity = models.PositiveIntegerField(
        "Количество", default=1, validators=[MinValueValidator(1)]
    )
    status = models.CharField("Статус", max_length=15, choices=STATUS_CHOICES, default="pending")
    note = models.TextField("Примечание", blank=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="order_quantity_gte_1",
            ),
        ]

    def __str__(self):
        return f"{self.name} — {self.get_status_display()}"


class Movement(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Оборудование")
    consumable = models.ForeignKey(Consumable, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Расходник")
    from_location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="movement_from", verbose_name="Откуда")
    to_location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="movement_to", verbose_name="Куда")
    from_operator = models.ForeignKey(Operator, on_delete=models.SET_NULL, null=True, blank=True, related_name="movement_from_op", verbose_name="От эксплуатанта")
    to_operator = models.ForeignKey(Operator, on_delete=models.SET_NULL, null=True, blank=True, related_name="movement_to_op", verbose_name="К эксплуатанту")
    note = models.TextField("Описание перемещения", blank=True)
    created_at = models.DateTimeField("Дата", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Кто изменил",
    )

    class Meta:
        verbose_name = "Перемещение"
        verbose_name_plural = "Перемещения"
        ordering = ["-created_at"]

    def __str__(self):
        parts = []
        if self.equipment:
            parts.append(str(self.equipment))
        if self.consumable:
            parts.append(str(self.consumable))
        parts.append(self.note or "перемещение")
        return " — ".join(parts)


class UserProfile(models.Model):
    NOTIFICATION_CHOICES = [
        ("none", "Не получать"),
        ("inventory", "Оборудование и расходники"),
        ("all", "Все события журнала"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inventory_profile",
        verbose_name="Пользователь",
    )
    avatar = models.ImageField(
        "Аватар", upload_to="avatars/%Y/%m/", blank=True
    )
    notification_level = models.CharField(
        "Уведомления",
        max_length=20,
        choices=NOTIFICATION_CHOICES,
        default="none",
    )

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

    def __str__(self):
        return f"Профиль {self.user.get_username()}"


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ("create", "Создание"),
        ("update", "Изменение"),
        ("delete", "Удаление"),
        ("repair_started", "Передано в ремонт"),
        ("repair_completed", "Ремонт завершён"),
        ("movement", "Архивное перемещение"),
        ("system", "Системное событие"),
    ]

    action = models.CharField(
        "Событие", max_length=30, choices=ACTION_CHOICES
    )
    object_type = models.CharField("Тип объекта", max_length=100, db_index=True)
    object_type_label = models.CharField("Раздел", max_length=150)
    object_id = models.CharField("ID объекта", max_length=64, blank=True)
    object_repr = models.CharField("Объект", max_length=500)
    changes = models.JSONField("Изменения", default=dict, blank=True)
    note = models.TextField("Комментарий", blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_audit_events",
        verbose_name="Пользователь",
    )
    actor_username = models.CharField("Логин пользователя", max_length=150, blank=True)
    actor_display_name = models.CharField(
        "Имя пользователя", max_length=300, blank=True
    )
    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True)
    created_at = models.DateTimeField(
        "Дата", default=timezone.now, editable=False, db_index=True
    )

    class Meta:
        verbose_name = "Событие аудита"
        verbose_name_plural = "Журнал аудита"
        ordering = ["-created_at", "-pk"]
        permissions = [
            ("manage_data_transfer", "Может импортировать и экспортировать данные"),
        ]
        indexes = [
            models.Index(fields=["object_type", "-created_at"]),
            models.Index(fields=["object_type", "object_id", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()}: {self.object_repr}"


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inventory_notifications",
        verbose_name="Пользователь",
    )
    audit_log = models.ForeignKey(
        AuditLog,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="Событие",
    )
    is_read = models.BooleanField("Прочитано", default=False)
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "audit_log"],
                name="unique_notification_per_event",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user}: {self.audit_log}"
