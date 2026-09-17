from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .audit import audit_is_suspended, consume_audit_note, get_audit_context
from .models import (
    AuditLog,
    Consumable,
    Disposal,
    DisposalDirection,
    Equipment,
    EquipmentType,
    Location,
    Notification,
    Operator,
    Order,
    Receipt,
    Supplier,
    UserProfile,
    WriteOff,
)


AUDITED_MODELS = (
    EquipmentType,
    Location,
    Supplier,
    DisposalDirection,
    Operator,
    Equipment,
    Consumable,
    Receipt,
    Disposal,
    WriteOff,
    Order,
)

INVENTORY_NOTIFICATION_MODELS = {
    "equipment",
    "consumable",
    "receipt",
    "disposal",
    "writeoff",
}

IGNORED_FIELDS = {"id", "created_at", "updated_at", "normalized_name_hash"}


def _field_value(instance, field):
    if field.is_relation:
        related_id = getattr(instance, field.attname)
        if related_id is None:
            return "—"
        try:
            return str(getattr(instance, field.name))
        except field.related_model.DoesNotExist:
            return str(related_id)

    display_method = getattr(instance, f"get_{field.name}_display", None)
    value = display_method() if display_method else getattr(instance, field.name)
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value)


def _snapshot(instance):
    return {
        field.name: {
            "label": str(field.verbose_name),
            "value": _field_value(instance, field),
        }
        for field in instance._meta.concrete_fields
        if field.name not in IGNORED_FIELDS
    }


def _current_actor():
    user = get_audit_context().get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def _create_notifications(event):
    profiles = UserProfile.objects.filter(user__is_active=True)
    audit_permission = Permission.objects.filter(
        content_type__app_label="inventory", codename="view_auditlog"
    ).first()
    if audit_permission is None:
        return
    profiles = profiles.filter(
        Q(user__is_superuser=True)
        | Q(user__user_permissions=audit_permission)
        | Q(user__groups__permissions=audit_permission)
    ).distinct()
    if event.object_type in INVENTORY_NOTIFICATION_MODELS:
        profiles = profiles.filter(
            Q(notification_level="inventory") | Q(notification_level="all")
        )
    else:
        profiles = profiles.filter(notification_level="all")
    if event.actor_id:
        profiles = profiles.exclude(user_id=event.actor_id)

    Notification.objects.bulk_create(
        [
            Notification(user_id=user_id, audit_log=event)
            for user_id in profiles.values_list("user_id", flat=True)
        ],
        ignore_conflicts=True,
    )


def record_audit_event(
    *,
    action,
    object_type,
    object_type_label,
    object_id="",
    object_repr,
    changes=None,
    note="",
    actor=None,
    ip_address=None,
):
    context = get_audit_context()
    resolved_actor = actor if actor is not None else _current_actor()
    event = AuditLog.objects.create(
        action=action,
        object_type=object_type,
        object_type_label=object_type_label,
        object_id=str(object_id or ""),
        object_repr=str(object_repr)[:500],
        changes=changes or {},
        note=note or "",
        actor=resolved_actor,
        actor_username=(resolved_actor.get_username() if resolved_actor else ""),
        actor_display_name=(
            resolved_actor.get_full_name() or resolved_actor.get_username()
            if resolved_actor
            else ""
        ),
        ip_address=ip_address if ip_address is not None else context.get("ip_address"),
    )
    _create_notifications(event)
    return event


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_delete, sender=UserProfile)
def delete_profile_avatar(sender, instance, **kwargs):
    if instance.avatar:
        storage = instance.avatar.storage
        name = instance.avatar.name
        transaction.on_commit(lambda: storage.delete(name))


@receiver(pre_save)
def capture_previous_state(sender, instance, **kwargs):
    if (
        kwargs.get("raw")
        or audit_is_suspended()
        or sender not in AUDITED_MODELS
        or not instance.pk
    ):
        return
    try:
        previous = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    instance._audit_previous_snapshot = _snapshot(previous)


@receiver(post_save)
def audit_model_save(sender, instance, created, **kwargs):
    if kwargs.get("raw") or audit_is_suspended() or sender not in AUDITED_MODELS:
        return

    current = _snapshot(instance)
    previous = getattr(instance, "_audit_previous_snapshot", {})
    changes = {}
    for field_name, field_data in current.items():
        old_value = previous.get(field_name, {}).get("value")
        new_value = field_data["value"]
        if created or old_value != new_value:
            changes[field_name] = {
                "label": field_data["label"],
                "old": old_value,
                "new": new_value,
            }

    if not created and not changes:
        return

    action = "create" if created else "update"
    if sender is Equipment and "status" in changes and not created:
        repair_label = dict(Equipment.STATUS_CHOICES)["repair"]
        if changes["status"]["new"] == repair_label:
            action = "repair_started"
        elif changes["status"]["old"] == repair_label:
            action = "repair_completed"

    record_audit_event(
        action=action,
        object_type=instance._meta.model_name,
        object_type_label=str(instance._meta.verbose_name),
        object_id=instance.pk,
        object_repr=instance,
        changes=changes,
        note=consume_audit_note(),
    )


@receiver(post_delete)
def audit_model_delete(sender, instance, **kwargs):
    if audit_is_suspended() or sender not in AUDITED_MODELS:
        return
    snapshot = _snapshot(instance)
    changes = {
        field_name: {
            "label": field_data["label"],
            "old": field_data["value"],
            "new": None,
        }
        for field_name, field_data in snapshot.items()
    }
    record_audit_event(
        action="delete",
        object_type=instance._meta.model_name,
        object_type_label=str(instance._meta.verbose_name),
        object_id=instance.pk,
        object_repr=instance,
        changes=changes,
        note=consume_audit_note(),
    )
