from django.contrib.auth.models import Group, Permission
from django.db.models import Q
from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate, dispatch_uid="inventory.ensure_default_roles")
def ensure_default_roles(sender, **kwargs):
    if sender.label != "inventory":
        return

    permissions = Permission.objects.filter(content_type__app_label="inventory")
    viewer, _ = Group.objects.get_or_create(name="viewer")
    editor, _ = Group.objects.get_or_create(name="editor")
    administrator, _ = Group.objects.get_or_create(name="administrator")

    viewer.permissions.add(*permissions.filter(codename__startswith="view_"))
    editor.permissions.add(*permissions.filter(codename__startswith="view_"))
    editor.permissions.add(
        *permissions.filter(
            Q(codename__startswith="add_")
            | Q(codename__startswith="change_")
        ).exclude(
            content_type__model__in=[
                "auditlog",
                "notification",
                "userprofile",
                "movement",
            ]
        )
    )
    administrator.permissions.add(*permissions)
