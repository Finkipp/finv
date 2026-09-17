import json
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .forms import EquipmentForm
from .models import (
    AuditLog,
    Consumable,
    Disposal,
    Equipment,
    EquipmentType,
    Location,
    Notification,
    UserProfile,
    consumable_name_hash,
)


User = get_user_model()


class InventoryTestMixin:
    def create_equipment_type(self, name="Ноутбук"):
        return EquipmentType.objects.create(name=name)

    def equipment_payload(self, equipment_type, **overrides):
        payload = {
            "equipment_type": equipment_type.pk,
            "name": "ThinkPad T14",
            "description": "Тестовое оборудование",
            "inventory_number": "iin1234567",
            "status": "working",
            "location": "",
            "operator": "",
            "change_note": "",
        }
        payload.update(overrides)
        return payload


class AccessControlTests(InventoryTestMixin, TestCase):
    def setUp(self):
        self.equipment_type = self.create_equipment_type()
        self.equipment = Equipment.objects.create(
            equipment_type=self.equipment_type,
            name="Рабочая станция",
            inventory_number="iin1000001",
        )

    def test_staff_without_model_permission_cannot_delete(self):
        staff = User.objects.create_user("staff", password="safe-pass-123", is_staff=True)
        self.client.force_login(staff)

        response = self.client.post(
            reverse("equipment_delete", args=[self.equipment.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Equipment.objects.filter(pk=self.equipment.pk).exists())

    def test_editor_role_can_add_but_cannot_delete(self):
        editor = User.objects.create_user("editor", password="safe-pass-123")
        editor.groups.add(Group.objects.get(name="editor"))
        self.client.force_login(editor)

        add_response = self.client.post(
            reverse("equipment_add"),
            self.equipment_payload(
                self.equipment_type,
                inventory_number="iin1000002",
            ),
        )
        delete_response = self.client.post(
            reverse("equipment_delete", args=[self.equipment.pk])
        )

        self.assertRedirects(add_response, reverse("equipment_list"))
        self.assertEqual(delete_response.status_code, 403)

    def test_unauthenticated_editor_route_redirects_to_login(self):
        response = self.client.get(reverse("equipment_add"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('equipment_add')}",
        )

    def test_external_login_redirect_is_rejected(self):
        user = User.objects.create_user("member", password="safe-pass-123")
        user.groups.add(Group.objects.get(name="viewer"))
        response = self.client.post(
            reverse("login") + "?next=https://example.invalid/phishing",
            {"username": "member", "password": "safe-pass-123"},
        )
        self.assertRedirects(response, reverse("dashboard"))

    def test_user_without_view_permission_cannot_open_inventory(self):
        user = User.objects.create_user("no-role", password="safe-pass-123")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("equipment_list")).status_code, 403)

    def test_receipt_permission_does_not_grant_equipment_creation(self):
        user = User.objects.create_user("receipt-only", password="safe-pass-123")
        user.user_permissions.add(Permission.objects.get(codename="add_receipt"))
        self.client.force_login(user)

        response = self.client.post(
            reverse("receipt_add"),
            {
                "date": "2026-08-24",
                "equipment_type": self.equipment_type.pk,
                "name": "Новый компьютер",
                "status": "new",
                "quantity": 1,
                "supplier": "",
                "create_target": "equipment",
                "eq_inventory_number": "iin1000010",
                "eq_description": "",
                "eq_location": "",
                "eq_operator": "",
                "eq_status": "working",
                "con_location": "",
                "con_status": "unknown",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Equipment.objects.filter(name="Новый компьютер").exists())

    def test_logout_requires_post(self):
        user = User.objects.create_user("member", password="safe-pass-123")
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertRedirects(
            self.client.post(reverse("logout")), reverse("login")
        )

    def test_default_roles_have_separate_permissions(self):
        editor = Group.objects.get(name="editor")
        administrator = Group.objects.get(name="administrator")

        self.assertTrue(editor.permissions.filter(codename="add_equipment").exists())
        self.assertTrue(editor.permissions.filter(codename="view_auditlog").exists())
        self.assertFalse(editor.permissions.filter(codename="delete_equipment").exists())
        self.assertTrue(
            administrator.permissions.filter(codename="delete_equipment").exists()
        )


class AuditLogTests(InventoryTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            "auditor", "auditor@example.test", "safe-pass-123"
        )
        self.equipment_type = self.create_equipment_type()
        self.client.force_login(self.user)

    def test_create_and_repair_workflow_is_audited_with_actor(self):
        create_response = self.client.post(
            reverse("equipment_add"),
            self.equipment_payload(self.equipment_type),
        )
        equipment = Equipment.objects.get(inventory_number="iin1234567")
        create_event = AuditLog.objects.get(
            object_type="equipment", object_id=str(equipment.pk), action="create"
        )

        repair_response = self.client.post(
            reverse("equipment_edit", args=[equipment.pk]),
            self.equipment_payload(
                self.equipment_type,
                status="repair",
                change_note="Замена системной платы",
            ),
        )
        repair_event = AuditLog.objects.get(
            object_type="equipment",
            object_id=str(equipment.pk),
            action="repair_started",
        )

        self.assertRedirects(create_response, reverse("equipment_list"))
        self.assertRedirects(repair_response, reverse("equipment_list"))
        self.assertEqual(create_event.actor, self.user)
        self.assertEqual(repair_event.actor, self.user)
        self.assertEqual(repair_event.note, "Замена системной платы")
        self.assertEqual(repair_event.changes["status"]["new"], "Ремонт")

    def test_leaving_repair_creates_completed_event(self):
        equipment = Equipment.objects.create(
            equipment_type=self.equipment_type,
            name="Сервер",
            inventory_number="iin1000003",
            status="repair",
        )
        self.client.post(
            reverse("equipment_edit", args=[equipment.pk]),
            self.equipment_payload(
                self.equipment_type,
                name="Сервер",
                inventory_number=equipment.inventory_number,
                status="working",
                change_note="Диагностика завершена",
            ),
        )

        event = AuditLog.objects.get(
            object_type="equipment",
            object_id=str(equipment.pk),
            action="repair_completed",
        )
        self.assertEqual(event.note, "Диагностика завершена")

    def test_delete_event_keeps_object_snapshot(self):
        equipment = Equipment.objects.create(
            equipment_type=self.equipment_type,
            name="Удаляемый ПК",
            inventory_number="iin1000004",
        )
        self.client.post(reverse("equipment_delete", args=[equipment.pk]))

        event = AuditLog.objects.get(
            object_type="equipment",
            object_id=str(equipment.pk),
            action="delete",
        )
        self.assertIn("Удаляемый ПК", event.object_repr)
        self.assertIn("inventory_number", event.changes)

    def test_actor_name_is_kept_after_user_deletion(self):
        equipment = Equipment.objects.create(
            equipment_type=self.equipment_type,
            name="Монитор",
            inventory_number="iin1000006",
        )
        self.client.post(
            reverse("equipment_edit", args=[equipment.pk]),
            self.equipment_payload(
                self.equipment_type,
                name="Монитор 27",
                inventory_number=equipment.inventory_number,
            ),
        )
        event = AuditLog.objects.filter(
            object_type="equipment", object_id=str(equipment.pk), action="update"
        ).latest("pk")

        self.user.delete()
        event.refresh_from_db()

        self.assertIsNone(event.actor)
        self.assertEqual(event.actor_username, "auditor")
        self.assertEqual(event.actor_display_name, "auditor")

    def test_noop_save_does_not_create_event(self):
        equipment = Equipment.objects.create(
            equipment_type=self.equipment_type,
            name="Без изменений",
            inventory_number="iin1000005",
        )
        event_count = AuditLog.objects.filter(
            object_type="equipment", object_id=str(equipment.pk)
        ).count()

        equipment.save()

        self.assertEqual(
            AuditLog.objects.filter(
                object_type="equipment", object_id=str(equipment.pk)
            ).count(),
            event_count,
        )

    def test_web_mutation_rolls_back_when_audit_fails(self):
        with patch(
            "inventory.signals.Notification.objects.bulk_create",
            side_effect=RuntimeError("notification failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse("equipment_add"),
                    self.equipment_payload(
                        self.equipment_type,
                        inventory_number="iin1000099",
                    ),
                )

        self.assertFalse(
            Equipment.objects.filter(inventory_number="iin1000099").exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(
                object_type="equipment", object_repr__contains="iin1000099"
            ).exists()
        )

    def test_legacy_movement_page_redirects_to_audit(self):
        response = self.client.get(reverse("movement_list"))
        self.assertRedirects(response, reverse("audit_log"))

    def test_audit_page_has_unified_object_column(self):
        response = self.client.get(reverse("audit_log"))
        self.assertContains(response, "Журнал аудита")
        self.assertContains(response, "Что изменилось")
        self.assertNotContains(response, "<th>Расходник</th>", html=True)


class NotificationTests(InventoryTestMixin, TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(
            "actor", "actor@example.test", "safe-pass-123"
        )
        self.inventory_subscriber = User.objects.create_user(
            "stock-user", password="safe-pass-123"
        )
        self.all_subscriber = User.objects.create_user(
            "all-user", password="safe-pass-123"
        )
        self.actor.inventory_profile.notification_level = "all"
        self.actor.inventory_profile.save()
        self.inventory_subscriber.inventory_profile.notification_level = "inventory"
        self.inventory_subscriber.inventory_profile.save()
        self.all_subscriber.inventory_profile.notification_level = "all"
        self.all_subscriber.inventory_profile.save()
        viewer = Group.objects.get(name="viewer")
        self.inventory_subscriber.groups.add(viewer)
        self.all_subscriber.groups.add(viewer)
        self.equipment_type = self.create_equipment_type()
        Notification.objects.all().delete()
        self.client.force_login(self.actor)

    def test_inventory_event_notifies_subscribers_except_actor(self):
        self.client.post(
            reverse("equipment_add"),
            self.equipment_payload(self.equipment_type),
        )
        event = AuditLog.objects.filter(
            object_type="equipment", action="create"
        ).latest("pk")

        self.assertTrue(
            Notification.objects.filter(
                user=self.inventory_subscriber, audit_log=event
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(user=self.all_subscriber, audit_log=event).exists()
        )
        self.assertFalse(
            Notification.objects.filter(user=self.actor, audit_log=event).exists()
        )

    def test_reference_event_only_notifies_full_subscriber(self):
        self.client.post(reverse("location_add"), {"name": "Склад 2"})
        event = AuditLog.objects.get(object_type="location", object_repr="Склад 2")

        self.assertFalse(
            Notification.objects.filter(
                user=self.inventory_subscriber, audit_log=event
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(user=self.all_subscriber, audit_log=event).exists()
        )

    def test_opening_notification_marks_it_read(self):
        self.client.post(
            reverse("equipment_add"),
            self.equipment_payload(self.equipment_type),
        )
        notification = Notification.objects.filter(user=self.all_subscriber).latest("pk")
        self.client.force_login(self.all_subscriber)

        response = self.client.post(
            reverse("notification_read", args=[notification.pk])
        )

        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertRedirects(
            response, reverse("audit_detail", args=[notification.audit_log_id])
        )


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "profile-user", password="old-safe-pass-123"
        )
        self.user.groups.add(Group.objects.get(name="viewer"))
        self.client.force_login(self.user)

    def test_profile_updates_personal_data_and_subscription(self):
        response = self.client.post(
            reverse("profile"),
            {
                "action": "profile",
                "first_name": "Иван",
                "last_name": "Петров",
                "email": "ivan@example.test",
                "notification_level": "all",
            },
        )

        self.user.refresh_from_db()
        self.user.inventory_profile.refresh_from_db()
        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(self.user.first_name, "Иван")
        self.assertEqual(self.user.inventory_profile.notification_level, "all")
        self.assertTrue(
            AuditLog.objects.filter(
                object_type="userprofile", object_id=str(self.user.pk), actor=self.user
            ).exists()
        )

    def test_password_change_keeps_current_session(self):
        response = self.client.post(
            reverse("profile"),
            {
                "action": "password",
                "old_password": "old-safe-pass-123",
                "new_password1": "new-even-safer-pass-456",
                "new_password2": "new-even-safer-pass-456",
            },
        )

        self.user.refresh_from_db()
        self.assertRedirects(response, reverse("profile"))
        self.assertTrue(self.user.check_password("new-even-safer-pass-456"))
        self.assertEqual(self.client.get(reverse("profile")).status_code, 200)

    def test_avatar_upload(self):
        gif = (
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
            b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00"
            b"\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        )
        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.post(
                reverse("profile"),
                {
                    "action": "profile",
                    "first_name": "",
                    "last_name": "",
                    "email": "",
                    "notification_level": "none",
                    "avatar": SimpleUploadedFile(
                        "avatar.gif", gif, content_type="image/gif"
                    ),
                },
            )
            self.user.inventory_profile.refresh_from_db()
            self.assertRedirects(response, reverse("profile"))
            self.assertTrue(self.user.inventory_profile.avatar.name.endswith(".gif"))

    def test_about_dialog_contains_version_and_authors(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Версия 4.1")
        self.assertContains(response, "Finkipp, DeepSeek Flash, GPT-5.6 Sol")


class BusinessRuleTests(InventoryTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            "manager", "manager@example.test", "safe-pass-123"
        )
        self.client.force_login(self.user)
        self.equipment_type = self.create_equipment_type("Картридж")

    def test_blank_inventory_number_is_generated(self):
        form = EquipmentForm(
            data=self.equipment_payload(
                self.equipment_type,
                inventory_number="",
            )
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertRegex(form.cleaned_data["inventory_number"], r"^iin\d{7}$")

    def test_expanding_unicode_casefold_uses_fixed_length_hash(self):
        consumable = Consumable(
            equipment_type=self.equipment_type,
            name="ß" * 255,
            quantity=1,
        )
        consumable.full_clean()
        consumable.save()
        self.assertEqual(len(consumable.normalized_name_hash), 64)

    def test_receipt_cannot_create_multiple_equipment_with_one_number(self):
        response = self.client.post(
            reverse("receipt_add"),
            {
                "date": "2026-08-24",
                "equipment_type": self.equipment_type.pk,
                "name": "Принтер",
                "status": "new",
                "quantity": 2,
                "supplier": "",
                "create_target": "equipment",
                "eq_inventory_number": "",
                "eq_description": "",
                "eq_location": "",
                "eq_operator": "",
                "eq_status": "working",
                "con_location": "",
                "con_status": "unknown",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "каждой единице нужен отдельный инвентарный номер")
        self.assertFalse(Equipment.objects.filter(name="Принтер").exists())

    def test_disposal_rejects_insufficient_stock_without_partial_record(self):
        consumable = Consumable.objects.create(
            equipment_type=self.equipment_type,
            name="Тонер",
            quantity=2,
        )
        response = self.client.post(
            reverse("disposal_add"),
            {
                "date": "2026-08-24",
                "equipment_type": self.equipment_type.pk,
                "name": "Тонер",
                "quantity": 5,
                "direction": "",
                "create_target": "consumable",
            },
        )

        consumable.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Недостаточно на складе")
        self.assertEqual(consumable.quantity, 2)
        self.assertFalse(Disposal.objects.exists())

    def test_receipt_updates_stock_atomically_and_records_audit(self):
        consumable = Consumable.objects.create(
            equipment_type=self.equipment_type,
            name="Тонер",
            quantity=2,
        )
        response = self.client.post(
            reverse("receipt_add"),
            {
                "date": "2026-08-24",
                "equipment_type": self.equipment_type.pk,
                "name": "ТОНЕР",
                "status": "new",
                "quantity": 3,
                "supplier": "",
                "create_target": "consumable",
                "eq_inventory_number": "",
                "eq_description": "",
                "eq_location": "",
                "eq_operator": "",
                "eq_status": "unknown",
                "con_location": "",
                "con_status": "working",
            },
        )

        consumable.refresh_from_db()
        self.assertRedirects(response, reverse("receipt_list"))
        self.assertEqual(consumable.quantity, 5)
        self.assertEqual(consumable.status, "working")
        event = AuditLog.objects.filter(
            object_type="consumable",
            object_id=str(consumable.pk),
            action="update",
        ).latest("pk")
        self.assertEqual(event.changes["quantity"], {
            "label": "Количество", "old": "2", "new": "5"
        })
        self.assertEqual(event.actor, self.user)

    def test_used_location_cannot_be_deleted(self):
        location = Location.objects.create(name="Склад")
        Equipment.objects.create(
            equipment_type=self.equipment_type,
            name="МФУ",
            inventory_number="iin1000007",
            location=location,
        )

        response = self.client.post(reverse("location_delete", args=[location.pk]))

        self.assertRedirects(response, reverse("location_list"))
        self.assertTrue(Location.objects.filter(pk=location.pk).exists())
        messages = list(response.wsgi_request._messages)
        self.assertTrue(any("используется" in str(message) for message in messages))


class ImportSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "importer", password="safe-pass-123", is_staff=True
        )
        permission = Permission.objects.get(codename="manage_data_transfer")
        self.user.user_permissions.add(permission)
        self.client.force_login(self.user)

    def test_import_rejects_auth_models(self):
        payload = [
            {
                "model": "auth.user",
                "pk": 999,
                "fields": {
                    "password": "",
                    "last_login": None,
                    "is_superuser": True,
                    "username": "injected-admin",
                    "first_name": "",
                    "last_name": "",
                    "email": "",
                    "is_staff": True,
                    "is_active": True,
                    "date_joined": "2026-08-24T00:00:00Z",
                    "groups": [],
                    "user_permissions": [],
                },
            }
        ]
        upload = SimpleUploadedFile(
            "data.json",
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )

        response = self.client.post(reverse("import_data"), {"file": upload})

        self.assertContains(response, "неподдерживаемую модель")
        self.assertFalse(User.objects.filter(username="injected-admin").exists())

    def test_valid_import_is_atomic_and_audited(self):
        payload = [
            {
                "model": "inventory.equipmenttype",
                "pk": 9001,
                "fields": {"name": "Импортированный тип"},
            }
        ]
        upload = SimpleUploadedFile(
            "valid.json",
            json.dumps(payload).encode("utf-8"),
            content_type="application/json",
        )

        response = self.client.post(reverse("import_data"), {"file": upload})

        self.assertContains(response, "Импортировано 1 записей")
        self.assertTrue(EquipmentType.objects.filter(pk=9001).exists())
        event = AuditLog.objects.get(object_type="data_import")
        self.assertEqual(event.actor, self.user)


class ConsumableMigrationTests(TransactionTestCase):
    migrate_from = (
        "inventory",
        "0005_auditlog_actor_display_name_auditlog_actor_username_and_more",
    )
    migrate_to = (
        "inventory",
        "0006_consumable_unique_consumable_position_with_location_and_more",
    )

    def test_duplicate_positions_are_merged_before_unique_constraint(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        equipment_type = old_apps.get_model("inventory", "EquipmentType").objects.create(
            name="Кабель"
        )
        consumable = old_apps.get_model("inventory", "Consumable")
        consumable.objects.create(
            equipment_type=equipment_type, name="HDMI", quantity=2
        )
        consumable.objects.create(
            equipment_type=equipment_type, name="hdmi", quantity=3
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps

        positions = new_apps.get_model("inventory", "Consumable").objects.all()
        self.assertEqual(positions.count(), 1)
        self.assertEqual(positions.get().quantity, 5)
        self.assertTrue(
            new_apps.get_model("inventory", "AuditLog").objects.filter(
                object_type="consumable", action="system"
            ).exists()
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


class UnicodeConsumableMigrationTests(TransactionTestCase):
    migrate_from = (
        "inventory",
        "0006_consumable_unique_consumable_position_with_location_and_more",
    )
    migrate_to = (
        "inventory",
        "0007_remove_consumable_unique_consumable_position_with_location_and_more",
    )

    def test_cyrillic_case_variants_are_merged(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        equipment_type = old_apps.get_model("inventory", "EquipmentType").objects.create(
            name="Тонер"
        )
        consumable = old_apps.get_model("inventory", "Consumable")
        consumable.objects.create(
            equipment_type=equipment_type, name="Тонер", quantity=2
        )
        consumable.objects.create(
            equipment_type=equipment_type, name="тонер", quantity=4
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps
        positions = new_apps.get_model("inventory", "Consumable").objects.all()

        self.assertEqual(positions.count(), 1)
        self.assertEqual(positions.get().quantity, 6)
        self.assertEqual(
            positions.get().normalized_name_hash,
            consumable_name_hash("тонер"),
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


class QuantityMigrationTests(TransactionTestCase):
    migrate_from = ("inventory", "0003_consumable_status_alter_disposal_date_and_more")
    migrate_to = ("inventory", "0004_auditlog_notification_userprofile_and_more")

    def test_zero_document_quantities_are_normalized_before_constraint(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        equipment_type = old_apps.get_model("inventory", "EquipmentType").objects.create(
            name="Старый тип"
        )
        old_apps.get_model("inventory", "Receipt").objects.create(
            equipment_type=equipment_type,
            name="Старое поступление",
            status="new",
            quantity=0,
        )
        old_apps.get_model("inventory", "Disposal").objects.create(
            equipment_type=equipment_type,
            name="Старый уход",
            quantity=0,
        )
        old_apps.get_model("inventory", "Order").objects.create(
            branch="Отдел",
            equipment_type=equipment_type,
            name="Старый заказ",
            quantity=0,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        new_apps = executor.loader.project_state([self.migrate_to]).apps

        self.assertEqual(
            new_apps.get_model("inventory", "Receipt").objects.get().quantity, 1
        )
        self.assertEqual(
            new_apps.get_model("inventory", "Disposal").objects.get().quantity, 1
        )
        self.assertEqual(
            new_apps.get_model("inventory", "Order").objects.get().quantity, 1
        )
        self.assertTrue(
            new_apps.get_model("inventory", "AuditLog").objects.filter(
                object_type="data_migration"
            ).exists()
        )

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()
