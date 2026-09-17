import csv
import json
from itertools import chain

from django.conf import settings
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import serializers
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView, View
from django.views.decorators.http import require_GET, require_POST
from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce

from .audit import set_audit_note, suspend_audit
from .models import (
    Equipment, Consumable, Receipt, Disposal, WriteOff, Order, Movement,
    EquipmentType, Location, Supplier, DisposalDirection, Operator,
    AuditLog, Notification, UserProfile, consumable_name_hash,
)
from .forms import (
    EquipmentForm, ConsumableForm, ReceiptForm, DisposalForm, WriteOffForm,
    OrderForm, ProfileUserForm, UserProfileForm,
)
from .signals import record_audit_event


DASHBOARD_PERMISSIONS = (
    "inventory.view_equipment",
    "inventory.view_consumable",
    "inventory.view_receipt",
    "inventory.view_writeoff",
)


def user_login(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        if not username or not password:
            return render(
                request,
                "inventory/login.html",
                {"error": "Введите имя пользователя и пароль."},
                status=400,
            )
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            default_url = "/" if user.has_perms(DASHBOARD_PERMISSIONS) else reverse("profile")
            next_url = request.POST.get("next") or request.GET.get("next") or default_url
            if not url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts=set(),
                require_https=request.is_secure(),
            ):
                next_url = default_url
            return redirect(next_url)
        return render(request, "inventory/login.html", {"error": "Неверное имя пользователя или пароль"})
    return render(request, "inventory/login.html")


@login_required
@require_POST
def user_logout(request):
    logout(request)
    return redirect("login")


class StaffRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        permission = f"{self.model._meta.app_label}.delete_{self.model._meta.model_name}"
        if not request.user.has_perm(permission):
            return self.handle_no_permission()
        if request.method == "POST":
            with transaction.atomic():
                return super().dispatch(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                "Запись используется в других разделах. Сначала измените связанные записи.",
            )
            return redirect(self.get_success_url())

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == "POST":
            return queryset.select_for_update()
        return queryset


class EditorRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        action = "add" if isinstance(self, CreateView) else "change"
        permission = f"{self.model._meta.app_label}.{action}_{self.model._meta.model_name}"
        if not request.user.has_perm(permission):
            return self.handle_no_permission()
        if request.method == "POST":
            with transaction.atomic():
                return super().dispatch(request, *args, **kwargs)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method == "POST" and isinstance(self, UpdateView):
            return queryset.select_for_update()
        return queryset


class ViewRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        permission = f"{self.model._meta.app_label}.view_{self.model._meta.model_name}"
        if not request.user.has_perm(permission):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


def get_sidebar_context(request):
    return {"current_user": request.user}


def update_consumable_stock(consumable, delta, *, status=None, note=""):
    queryset = Consumable.objects.filter(pk=consumable.pk)
    if delta < 0:
        queryset = queryset.filter(quantity__gte=-delta)

    updates = {
        "quantity": F("quantity") + delta,
        "updated_at": timezone.now(),
    }
    old_status = consumable.status
    if status:
        updates["status"] = status

    if not queryset.update(**updates):
        current = Consumable.objects.filter(pk=consumable.pk).first()
        if current is None:
            raise ValidationError("Расходник больше не найден.")
        raise ValidationError(f"Недостаточно на складе. Доступно: {current.quantity}.")

    consumable.refresh_from_db()
    changes = {
        "quantity": {
            "label": "Количество",
            "old": str(consumable.quantity - delta),
            "new": str(consumable.quantity),
        }
    }
    if status and old_status != consumable.status:
        status_labels = dict(Consumable.STATUS_CHOICES)
        changes["status"] = {
            "label": "Статус",
            "old": status_labels.get(old_status, old_status),
            "new": status_labels.get(consumable.status, consumable.status),
        }
    record_audit_event(
        action="update",
        object_type="consumable",
        object_type_label="Расходник",
        object_id=consumable.pk,
        object_repr=consumable,
        changes=changes,
        note=note,
    )


# ─── Dashboard ───────────────────────────────────────────────────────────────

@login_required
@permission_required(
    DASHBOARD_PERMISSIONS,
    raise_exception=True,
)
def dashboard(request):
    total_equipment = Equipment.objects.count()
    working_equipment = Equipment.objects.filter(status="working").count()
    broken_equipment = Equipment.objects.filter(status="broken").count()
    total_consumables = Consumable.objects.aggregate(t=Coalesce(Sum("quantity"), 0))["t"]
    total_consumable_items = Consumable.objects.count()
    recent_equipment = Equipment.objects.order_by("-created_at")[:5]
    recent_receipts = Receipt.objects.order_by("-date")[:5]
    recent_writeoffs = WriteOff.objects.order_by("-actual_date")[:5]
    low_stock = Consumable.objects.filter(quantity__lt=5)

    unknown_equipment = Equipment.objects.filter(status="unknown").count()
    repair_equipment = Equipment.objects.filter(status="repair").count()

    ctx = get_sidebar_context(request)
    ctx.update({
        "total_equipment": total_equipment,
        "working_equipment": working_equipment,
        "broken_equipment": broken_equipment,
        "unknown_equipment": unknown_equipment,
        "repair_equipment": repair_equipment,
        "total_consumables": total_consumables,
        "total_consumable_items": total_consumable_items,
        "recent_equipment": recent_equipment,
        "recent_receipts": recent_receipts,
        "recent_writeoffs": recent_writeoffs,
        "low_stock": low_stock,
        "section": "dashboard",
    })
    return render(request, "inventory/dashboard.html", ctx)


# ─── Equipment ───────────────────────────────────────────────────────────────

class EquipmentListView(ViewRequiredMixin, ListView):
    model = Equipment
    template_name = "inventory/equipment_list.html"
    context_object_name = "equipment_list"
    paginate_by = 25

    def get_queryset(self):
        qs = Equipment.objects.select_related("equipment_type", "location", "operator")
        q = self.request.GET.get("q", "")
        if q:
            qs = qs.filter(
                Q(inventory_number__icontains=q) |
                Q(name__icontains=q) |
                Q(location__name__icontains=q) |
                Q(description__icontains=q)
            )
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        eq_type = self.request.GET.get("equipment_type", "")
        if eq_type.isdigit():
            qs = qs.filter(equipment_type_id=eq_type)
        loc = self.request.GET.get("location", "")
        if loc.isdigit():
            qs = qs.filter(location_id=loc)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["section"] = "equipment"
        ctx["filter_status"] = self.request.GET.get("status", "")
        ctx["filter_equipment_type"] = self.request.GET.get("equipment_type", "")
        ctx["filter_location"] = self.request.GET.get("location", "")
        ctx["equipment_types"] = EquipmentType.objects.all()
        ctx["locations"] = Location.objects.all()
        ctx["statuses"] = Equipment.STATUS_CHOICES
        return ctx


class EquipmentDetailView(ViewRequiredMixin, DetailView):
    model = Equipment
    template_name = "inventory/equipment_detail.html"
    context_object_name = "eq"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "equipment"
        if self.request.user.has_perm("inventory.view_auditlog"):
            ctx["audit_events"] = AuditLog.objects.filter(
                object_type="equipment", object_id=str(self.object.pk)
            ).select_related("actor")[:10]
        return ctx


class EquipmentCreateView(EditorRequiredMixin, CreateView):
    model = Equipment
    form_class = EquipmentForm
    template_name = "inventory/equipment_form.html"
    success_url = reverse_lazy("equipment_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "equipment"
        return ctx


class EquipmentUpdateView(EditorRequiredMixin, UpdateView):
    model = Equipment
    form_class = EquipmentForm
    template_name = "inventory/equipment_form.html"
    success_url = reverse_lazy("equipment_list")

    def form_valid(self, form):
        set_audit_note(form.cleaned_data.get("change_note"))
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "equipment"
        return ctx


class EquipmentDeleteView(StaffRequiredMixin, DeleteView):
    model = Equipment
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("equipment_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "equipment"
        return ctx


@login_required
@permission_required("inventory.add_equipment", raise_exception=True)
@require_GET
def generate_inventory_number(request):
    inv_num = Equipment.generate_inventory_number()
    return JsonResponse({"inventory_number": inv_num})


# ─── Consumables ─────────────────────────────────────────────────────────────

class ConsumableListView(ViewRequiredMixin, ListView):
    model = Consumable
    template_name = "inventory/consumable_list.html"
    context_object_name = "consumable_list"
    paginate_by = 100

    def get_queryset(self):
        qs = Consumable.objects.select_related("equipment_type", "location")
        q = self.request.GET.get("q", "")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(equipment_type__name__icontains=q) |
                Q(location__name__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["section"] = "consumables"
        groups = {}
        for c in ctx["object_list"]:
            t = c.equipment_type
            groups.setdefault(t, []).append(c)
        ctx["groups"] = sorted(groups.items(), key=lambda x: x[0].name)
        return ctx


class ConsumableCreateView(EditorRequiredMixin, CreateView):
    model = Consumable
    form_class = ConsumableForm
    template_name = "inventory/consumable_form.html"
    success_url = reverse_lazy("consumable_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "consumables"
        return ctx


class ConsumableUpdateView(EditorRequiredMixin, UpdateView):
    model = Consumable
    form_class = ConsumableForm
    template_name = "inventory/consumable_form.html"
    success_url = reverse_lazy("consumable_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "consumables"
        return ctx


class ConsumableDeleteView(StaffRequiredMixin, DeleteView):
    model = Consumable
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("consumable_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "consumables"
        return ctx


# ─── Receipts ────────────────────────────────────────────────────────────────

class ReceiptListView(ViewRequiredMixin, ListView):
    model = Receipt
    template_name = "inventory/receipt_list.html"
    context_object_name = "receipt_list"
    paginate_by = 25

    def get_queryset(self):
        qs = Receipt.objects.select_related("equipment_type", "supplier")
        q = self.request.GET.get("q", "")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(equipment_type__name__icontains=q) |
                Q(supplier__name__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["section"] = "receipts"
        return ctx


class ReceiptCreateView(EditorRequiredMixin, CreateView):
    model = Receipt
    form_class = ReceiptForm
    template_name = "inventory/receipt_form.html"
    success_url = reverse_lazy("receipt_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "receipts"
        return ctx

    def form_valid(self, form):
        with transaction.atomic():
            self.object = form.save()
            self._create_related(form)
        return HttpResponseRedirect(self.get_success_url())

    def _create_related(self, form):
        target = form.cleaned_data.get("create_target", "none")
        if target == "none":
            return
        eq_type = form.cleaned_data["equipment_type"]
        name = form.cleaned_data["name"]
        qty = form.cleaned_data["quantity"]

        if target == "equipment":
            if not self.request.user.has_perm("inventory.add_equipment"):
                raise PermissionDenied
            inv = form.cleaned_data.get("eq_inventory_number") or Equipment.generate_inventory_number()
            Equipment.objects.create(
                equipment_type=eq_type,
                name=name,
                inventory_number=inv,
                status=form.cleaned_data.get("eq_status", "unknown"),
                description=form.cleaned_data.get("eq_description", ""),
                location=form.cleaned_data.get("eq_location"),
                operator=form.cleaned_data.get("eq_operator"),
            )
        elif target == "consumable":
            location = form.cleaned_data.get("con_location")
            name_hash = consumable_name_hash(name)
            cons = Consumable.objects.select_for_update().filter(
                equipment_type=eq_type,
                normalized_name_hash=name_hash,
                location=location,
            ).first()
            if cons:
                if not self.request.user.has_perm("inventory.change_consumable"):
                    raise PermissionDenied
                update_consumable_stock(
                    cons,
                    qty,
                    status=form.cleaned_data.get("con_status"),
                    note=f"Поступление № {self.object.pk}",
                )
            else:
                if not self.request.user.has_perm("inventory.add_consumable"):
                    raise PermissionDenied
                try:
                    with transaction.atomic():
                        Consumable.objects.create(
                            equipment_type=eq_type,
                            name=name,
                            quantity=qty,
                            status=form.cleaned_data.get("con_status") or "unknown",
                            location=location,
                        )
                except IntegrityError:
                    cons = Consumable.objects.select_for_update().get(
                        equipment_type=eq_type,
                        normalized_name_hash=name_hash,
                        location=location,
                    )
                    if not self.request.user.has_perm("inventory.change_consumable"):
                        raise PermissionDenied
                    update_consumable_stock(
                        cons,
                        qty,
                        status=form.cleaned_data.get("con_status"),
                        note=f"Поступление № {self.object.pk}",
                    )


class ReceiptUpdateView(EditorRequiredMixin, UpdateView):
    model = Receipt
    form_class = ReceiptForm
    template_name = "inventory/receipt_form.html"
    success_url = reverse_lazy("receipt_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "receipts"
        return ctx


class ReceiptDeleteView(StaffRequiredMixin, DeleteView):
    model = Receipt
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("receipt_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "receipts"
        return ctx


# ─── Disposals ───────────────────────────────────────────────────────────────

class DisposalListView(ViewRequiredMixin, ListView):
    model = Disposal
    template_name = "inventory/disposal_list.html"
    context_object_name = "disposal_list"
    paginate_by = 25

    def get_queryset(self):
        qs = Disposal.objects.select_related("equipment_type", "direction")
        q = self.request.GET.get("q", "")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(equipment_type__name__icontains=q) |
                Q(direction__name__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["section"] = "disposals"
        return ctx


class DisposalCreateView(EditorRequiredMixin, CreateView):
    model = Disposal
    form_class = DisposalForm
    template_name = "inventory/disposal_form.html"
    success_url = reverse_lazy("disposal_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "disposals"
        return ctx

    def form_valid(self, form):
        target = form.cleaned_data.get("create_target", "none")
        related_permission = {
            "equipment": "inventory.change_equipment",
            "consumable": "inventory.change_consumable",
        }.get(target)
        if related_permission and not self.request.user.has_perm(related_permission):
            raise PermissionDenied
        try:
            with transaction.atomic():
                self.object = form.save()
                self._apply_related(form)
        except ValidationError as error:
            form.add_error(None, error.message)
            return self.form_invalid(form)
        return HttpResponseRedirect(self.get_success_url())

    def _apply_related(self, form):
        target = form.cleaned_data.get("create_target", "none")
        if target == "none":
            return
        eq_type = form.cleaned_data["equipment_type"]
        name = form.cleaned_data["name"]
        qty = form.cleaned_data["quantity"]

        if target == "equipment":
            try:
                equip = Equipment.objects.select_for_update().get(
                    equipment_type=eq_type, name__iexact=name
                )
            except Equipment.DoesNotExist as error:
                raise ValidationError("Оборудование больше не найдено.") from error
            equip.status = "broken"
            equip.save()
        elif target == "consumable":
            try:
                cons = Consumable.objects.select_for_update().get(
                    equipment_type=eq_type,
                    normalized_name_hash=consumable_name_hash(name),
                )
            except Consumable.DoesNotExist as error:
                raise ValidationError("Расходник больше не найден.") from error
            update_consumable_stock(
                cons,
                -qty,
                note=f"Уход № {self.object.pk}",
            )


class DisposalUpdateView(EditorRequiredMixin, UpdateView):
    model = Disposal
    form_class = DisposalForm
    template_name = "inventory/disposal_form.html"
    success_url = reverse_lazy("disposal_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "disposals"
        return ctx


class DisposalDeleteView(StaffRequiredMixin, DeleteView):
    model = Disposal
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("disposal_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "disposals"
        return ctx


# ─── WriteOffs ───────────────────────────────────────────────────────────────

class WriteOffListView(ViewRequiredMixin, ListView):
    model = WriteOff
    template_name = "inventory/writeoff_list.html"
    context_object_name = "writeoff_list"
    paginate_by = 25

    def get_queryset(self):
        qs = WriteOff.objects.all()
        q = self.request.GET.get("q", "")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(reason__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["section"] = "writeoffs"
        return ctx


class WriteOffCreateView(EditorRequiredMixin, CreateView):
    model = WriteOff
    form_class = WriteOffForm
    template_name = "inventory/writeoff_form.html"
    success_url = reverse_lazy("writeoff_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "writeoffs"
        return ctx


class WriteOffUpdateView(EditorRequiredMixin, UpdateView):
    model = WriteOff
    form_class = WriteOffForm
    template_name = "inventory/writeoff_form.html"
    success_url = reverse_lazy("writeoff_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "writeoffs"
        return ctx


class WriteOffDeleteView(StaffRequiredMixin, DeleteView):
    model = WriteOff
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("writeoff_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "writeoffs"
        return ctx


# ─── Reference Books (Справочники) ───────────────────────────────────────────

class RefMixin:
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "refs"
        opts = self.model._meta
        ctx["can_add"] = self.request.user.has_perm(
            f"{opts.app_label}.add_{opts.model_name}"
        )
        ctx["can_change"] = self.request.user.has_perm(
            f"{opts.app_label}.change_{opts.model_name}"
        )
        ctx["can_delete"] = self.request.user.has_perm(
            f"{opts.app_label}.delete_{opts.model_name}"
        )
        return ctx


class EquipmentTypeListView(ViewRequiredMixin, RefMixin, ListView):
    model = EquipmentType
    template_name = "inventory/ref_list.html"
    context_object_name = "items"
    extra_context = {"ref_name": "Типы оборудования/расходников", "ref_url": "eqtype"}


class EquipmentTypeCreateView(EditorRequiredMixin, RefMixin, CreateView):
    model = EquipmentType
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("eqtype_list")
    extra_context = {"ref_name": "Тип оборудования/расходника", "ref_url": "eqtype"}


class EquipmentTypeUpdateView(EditorRequiredMixin, RefMixin, UpdateView):
    model = EquipmentType
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("eqtype_list")
    extra_context = {"ref_name": "Тип оборудования/расходника", "ref_url": "eqtype"}


class EquipmentTypeDeleteView(StaffRequiredMixin, RefMixin, DeleteView):
    model = EquipmentType
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("eqtype_list")


class LocationListView(ViewRequiredMixin, RefMixin, ListView):
    model = Location
    template_name = "inventory/ref_list.html"
    context_object_name = "items"
    extra_context = {"ref_name": "Местоположения", "ref_url": "location"}


class LocationCreateView(EditorRequiredMixin, RefMixin, CreateView):
    model = Location
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("location_list")
    extra_context = {"ref_name": "Местоположение", "ref_url": "location"}


class LocationUpdateView(EditorRequiredMixin, RefMixin, UpdateView):
    model = Location
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("location_list")
    extra_context = {"ref_name": "Местоположение", "ref_url": "location"}


class LocationDeleteView(StaffRequiredMixin, RefMixin, DeleteView):
    model = Location
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("location_list")


class SupplierListView(ViewRequiredMixin, RefMixin, ListView):
    model = Supplier
    template_name = "inventory/ref_list.html"
    context_object_name = "items"
    extra_context = {"ref_name": "Поставщики", "ref_url": "supplier"}


class SupplierCreateView(EditorRequiredMixin, RefMixin, CreateView):
    model = Supplier
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("supplier_list")
    extra_context = {"ref_name": "Поставщик", "ref_url": "supplier"}


class SupplierUpdateView(EditorRequiredMixin, RefMixin, UpdateView):
    model = Supplier
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("supplier_list")
    extra_context = {"ref_name": "Поставщик", "ref_url": "supplier"}


class SupplierDeleteView(StaffRequiredMixin, RefMixin, DeleteView):
    model = Supplier
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("supplier_list")


class DisposalDirectionListView(ViewRequiredMixin, RefMixin, ListView):
    model = DisposalDirection
    template_name = "inventory/ref_list.html"
    context_object_name = "items"
    extra_context = {"ref_name": "Направления ухода", "ref_url": "direction"}


class DisposalDirectionCreateView(EditorRequiredMixin, RefMixin, CreateView):
    model = DisposalDirection
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("direction_list")
    extra_context = {"ref_name": "Направление ухода", "ref_url": "direction"}


class DisposalDirectionUpdateView(EditorRequiredMixin, RefMixin, UpdateView):
    model = DisposalDirection
    fields = ["name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("direction_list")
    extra_context = {"ref_name": "Направление ухода", "ref_url": "direction"}


class DisposalDirectionDeleteView(StaffRequiredMixin, RefMixin, DeleteView):
    model = DisposalDirection
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("direction_list")


class OperatorListView(ViewRequiredMixin, RefMixin, ListView):
    model = Operator
    template_name = "inventory/ref_list.html"
    context_object_name = "items"
    extra_context = {"ref_name": "Эксплуатанты", "ref_url": "operator"}


class OperatorCreateView(EditorRequiredMixin, RefMixin, CreateView):
    model = Operator
    fields = ["full_name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("operator_list")
    extra_context = {"ref_name": "Эксплуатант", "ref_url": "operator"}


class OperatorUpdateView(EditorRequiredMixin, RefMixin, UpdateView):
    model = Operator
    fields = ["full_name"]
    template_name = "inventory/ref_form.html"
    success_url = reverse_lazy("operator_list")
    extra_context = {"ref_name": "Эксплуатант", "ref_url": "operator"}


class OperatorDeleteView(StaffRequiredMixin, RefMixin, DeleteView):
    model = Operator
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("operator_list")


# ─── Orders ────────────────────────────────────────────────────────────────

class OrderListView(ViewRequiredMixin, ListView):
    model = Order
    template_name = "inventory/order_list.html"
    context_object_name = "order_list"
    paginate_by = 25

    def get_queryset(self):
        qs = Order.objects.select_related("equipment_type")
        q = self.request.GET.get("q", "")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) |
                Q(branch__icontains=q) |
                Q(equipment_type__name__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["section"] = "orders"
        return ctx


class OrderCreateView(EditorRequiredMixin, CreateView):
    model = Order
    form_class = OrderForm
    template_name = "inventory/order_form.html"
    success_url = reverse_lazy("order_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "orders"
        return ctx


class OrderUpdateView(EditorRequiredMixin, UpdateView):
    model = Order
    form_class = OrderForm
    template_name = "inventory/order_form.html"
    success_url = reverse_lazy("order_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "orders"
        return ctx


class OrderDeleteView(StaffRequiredMixin, DeleteView):
    model = Order
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("order_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "orders"
        return ctx


# ─── Audit log ─────────────────────────────────────────────────────────────

class AuditLogListView(ViewRequiredMixin, ListView):
    model = AuditLog
    template_name = "inventory/audit_log.html"
    context_object_name = "audit_events"
    paginate_by = 30

    def get_queryset(self):
        qs = AuditLog.objects.select_related("actor")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(object_repr__icontains=q)
                | Q(object_type_label__icontains=q)
                | Q(note__icontains=q)
                | Q(actor__username__icontains=q)
                | Q(actor__first_name__icontains=q)
                | Q(actor__last_name__icontains=q)
                | Q(actor_username__icontains=q)
                | Q(actor_display_name__icontains=q)
            )
        action = self.request.GET.get("action", "")
        if action in dict(AuditLog.ACTION_CHOICES):
            qs = qs.filter(action=action)
        object_type = self.request.GET.get("object_type", "")
        if object_type:
            qs = qs.filter(object_type=object_type)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["filter_action"] = self.request.GET.get("action", "")
        ctx["filter_object_type"] = self.request.GET.get("object_type", "")
        ctx["actions"] = AuditLog.ACTION_CHOICES
        ctx["object_types"] = AuditLog.objects.order_by(
            "object_type_label"
        ).values_list("object_type", "object_type_label").distinct()
        ctx["section"] = "audit"
        return ctx


class AuditLogDetailView(ViewRequiredMixin, DetailView):
    model = AuditLog
    template_name = "inventory/audit_detail.html"
    context_object_name = "event"

    def get_queryset(self):
        return super().get_queryset().select_related("actor")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["section"] = "audit"
        return ctx


@login_required
def legacy_movement_redirect(request, **kwargs):
    return redirect("audit_log")


# ─── Profile and notifications ─────────────────────────────────────────────

def _style_password_form(form):
    for field in form.fields.values():
        field.widget.attrs["class"] = "form-control"


@login_required
@transaction.atomic
def profile(request):
    request.user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
    try:
        user_profile = UserProfile.objects.select_for_update().get(user=request.user)
    except UserProfile.DoesNotExist:
        user_profile = UserProfile.objects.create(user=request.user)
    old_avatar_name = user_profile.avatar.name if user_profile.avatar else ""
    avatar_storage = user_profile.avatar.storage
    old_values = {
        "first_name": request.user.first_name,
        "last_name": request.user.last_name,
        "email": request.user.email,
        "avatar": user_profile.avatar.name if user_profile.avatar else "",
        "notification_level": user_profile.get_notification_level_display(),
    }
    action = request.POST.get("action") if request.method == "POST" else None

    user_form = ProfileUserForm(
        request.POST if action == "profile" else None,
        instance=request.user,
    )
    profile_form = UserProfileForm(
        request.POST if action == "profile" else None,
        request.FILES if action == "profile" else None,
        instance=user_profile,
    )
    if not request.user.has_perm("inventory.view_auditlog"):
        profile_form.fields["notification_level"].disabled = True
    password_form = PasswordChangeForm(
        request.user,
        request.POST if action == "password" else None,
    )
    _style_password_form(password_form)

    if action == "profile" and user_form.is_valid() and profile_form.is_valid():
        with transaction.atomic():
            user_form.save()
            profile_form.save()
            new_avatar_name = user_profile.avatar.name if user_profile.avatar else ""
            if old_avatar_name and old_avatar_name != new_avatar_name:
                transaction.on_commit(
                    lambda: avatar_storage.delete(old_avatar_name)
                )

            field_labels = {
                "first_name": "Имя",
                "last_name": "Фамилия",
                "email": "Электронная почта",
                "avatar": "Аватар",
                "notification_level": "Уведомления",
            }
            new_values = {
                "first_name": request.user.first_name,
                "last_name": request.user.last_name,
                "email": request.user.email,
                "avatar": user_profile.avatar.name if user_profile.avatar else "",
                "notification_level": user_profile.get_notification_level_display(),
            }
            changes = {
                name: {
                    "label": field_labels[name],
                    "old": old_values[name] or "—",
                    "new": new_values[name] or "—",
                }
                for name in field_labels
                if old_values[name] != new_values[name]
            }
            if changes:
                record_audit_event(
                    action="update",
                    object_type="userprofile",
                    object_type_label="Профиль пользователя",
                    object_id=request.user.pk,
                    object_repr=request.user.get_full_name() or request.user.get_username(),
                    changes=changes,
                    actor=request.user,
                )
        messages.success(request, "Профиль обновлён.")
        return redirect("profile")

    if action == "password" and password_form.is_valid():
        user = password_form.save()
        update_session_auth_hash(request, user)
        record_audit_event(
            action="system",
            object_type="userprofile",
            object_type_label="Профиль пользователя",
            object_id=user.pk,
            object_repr=user.get_full_name() or user.get_username(),
            changes={
                "password": {
                    "label": "Пароль",
                    "old": "Скрыто",
                    "new": "Изменён",
                }
            },
            note="Пользователь изменил пароль",
            actor=user,
        )
        messages.success(request, "Пароль изменён.")
        return redirect("profile")

    groups = list(request.user.groups.values_list("name", flat=True))
    if request.user.is_superuser:
        role_name = "Суперпользователь"
    elif groups:
        role_name = ", ".join(groups)
    else:
        role_name = "Пользователь только для чтения"

    return render(
        request,
        "inventory/profile.html",
        {
            "user_form": user_form,
            "profile_form": profile_form,
            "password_form": password_form,
            "role_name": role_name,
            "section": "profile",
        },
    )


class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = "inventory/notification_list.html"
    context_object_name = "notifications"
    paginate_by = 30

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.has_perm("inventory.view_auditlog"):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).select_related(
            "audit_log", "audit_log__actor"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["section"] = "profile"
        return ctx


@login_required
@permission_required("inventory.view_auditlog", raise_exception=True)
@require_POST
def notification_mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    return redirect("audit_detail", pk=notification.audit_log_id)


@login_required
@permission_required("inventory.view_auditlog", raise_exception=True)
@require_POST
def notifications_mark_all_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect("notification_list")


# ─── Export ─────────────────────────────────────────────────────────────────

class ExportCSVView(LoginRequiredMixin, View):
    def get(self, request, model_name):
        model_map = {
            "equipment": (Equipment, [
                ("Инвентарный номер", "inventory_number"), ("Наименование", "name"),
                ("Тип", "equipment_type__name"), ("Статус", "status"),
                ("Местоположение", "location__name"), ("Эксплуатант", "operator__full_name"),
                ("Описание", "description"),
            ]),
            "consumable": (Consumable, [
                ("Наименование", "name"), ("Тип", "equipment_type__name"),
                ("Количество", "quantity"), ("Местоположение", "location__name"),
            ]),
            "receipt": (Receipt, [
                ("Дата", "date"), ("Наименование", "name"),
                ("Тип", "equipment_type__name"), ("Статус", "status"),
                ("Количество", "quantity"), ("Поставщик", "supplier__name"),
            ]),
            "disposal": (Disposal, [
                ("Дата", "date"), ("Наименование", "name"),
                ("Тип", "equipment_type__name"), ("Количество", "quantity"),
                ("Направление", "direction__name"),
            ]),
            "writeoff": (WriteOff, [
                ("Наименование", "name"), ("Тип объекта", "object_type"),
                ("Причина", "reason"), ("Статус", "status"),
                ("Фактическая дата", "actual_date"), ("Итоговая дата", "final_date"),
            ]),
            "order": (Order, [
                ("Филиал", "branch"), ("Наименование", "name"),
                ("Тип", "equipment_type__name"), ("Количество", "quantity"),
                ("Статус", "status"), ("Дата", "created_at"),
            ]),
            "audit": (AuditLog, [
                ("Дата", "created_at"), ("Событие", "get_action_display"),
                ("Раздел", "object_type_label"), ("Объект", "object_repr"),
                ("Пользователь", "actor_display_name"), ("Комментарий", "note"),
                ("Изменения", "changes"),
            ]),
        }
        if model_name not in model_map:
            return JsonResponse({"error": "unknown model"}, status=404)
        model_class, columns = model_map[model_name]
        permission = (
            f"{model_class._meta.app_label}.view_{model_class._meta.model_name}"
        )
        if not request.user.has_perm(permission):
            return JsonResponse({"error": "forbidden"}, status=403)
        qs = model_class.objects.all()
        related_fields = {
            field_path.rsplit("__", 1)[0]
            for _, field_path in columns
            if "__" in field_path
        }
        if related_fields:
            qs = qs.select_related(*related_fields)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{model_name}.csv"'
        response.write("\ufeff".encode("utf-8"))
        writer = csv.writer(response)
        writer.writerow([header for header, _ in columns])
        for obj in qs:
            row = []
            for _, field_path in columns:
                parts = field_path.split("__")
                val = obj
                for p in parts:
                    val = getattr(val, p, "") if val else ""
                if callable(val):
                    val = val()
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                cell = str(val) if val is not None else ""
                if cell.startswith(("=", "+", "-", "@", "\t", "\r")):
                    cell = "'" + cell
                row.append(cell)
            writer.writerow(row)
        return response


# ─── Data Import / Export ─────────────────────────────────────────────────────

TRANSFER_MODELS = [
    EquipmentType, Location, Supplier, DisposalDirection, Operator,
    Equipment, Consumable, Receipt, Disposal, WriteOff, Order,
]


def can_manage_data_transfer(user):
    return user.has_perm("inventory.manage_data_transfer")

class ExportDataView(LoginRequiredMixin, View):
    def get(self, request):
        if not can_manage_data_transfer(request.user):
            return JsonResponse({"error": "forbidden"}, status=403)
        objects = chain.from_iterable(
            model.objects.all().iterator(chunk_size=1000)
            for model in TRANSFER_MODELS
        )
        data = serializers.serialize("json", objects, indent=2, use_natural_foreign_keys=True)
        response = HttpResponse(data, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="finv_data.json"'
        return response


class ImportDataView(LoginRequiredMixin, View):
    template_name = "inventory/data_management.html"

    def get(self, request):
        if not can_manage_data_transfer(request.user):
            return JsonResponse({"error": "forbidden"}, status=403)
        return render(request, self.template_name, {"section": "import"})

    def post(self, request):
        if not can_manage_data_transfer(request.user):
            return JsonResponse({"error": "forbidden"}, status=403)
        file = request.FILES.get("file")
        if not file:
            return render(request, self.template_name, {"section": "import", "error": "Файл не выбран."})
        if not file.name.endswith(".json"):
            return render(request, self.template_name, {"section": "import", "error": "Формат файла должен быть .json"})
        if file.size > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            return render(
                request,
                self.template_name,
                {"section": "import", "error": "Размер файла превышает 5 МБ."},
            )
        try:
            content = file.read().decode("utf-8")
            data = json.loads(content)
            if not isinstance(data, list):
                return render(request, self.template_name, {"section": "import", "error": "Неверный формат: ожидается список объектов."})
            allowed_models = {
                model._meta.label_lower: model for model in TRANSFER_MODELS
            }
            for item in data:
                if not isinstance(item, dict) or item.get("model", "").lower() not in allowed_models:
                    raise ValidationError("Файл содержит неподдерживаемую модель данных.")
                model = allowed_models[item["model"].lower()]
                pk = item.get("pk")
                if pk is not None and model.objects.filter(pk=pk).exists():
                    raise ValidationError(
                        f"Импорт остановлен: {item['model']} с ID {pk} уже существует."
                    )
            count = 0
            with transaction.atomic():
                with suspend_audit():
                    for obj in serializers.deserialize("json", json.dumps(data)):
                        obj.object.full_clean()
                        obj.save(save_m2m=True)
                        count += 1
                record_audit_event(
                    action="system",
                    object_type="data_import",
                    object_type_label="Импорт данных",
                    object_repr=f"Импортировано записей: {count}",
                    note=file.name,
                )
            return render(request, self.template_name, {"section": "import", "success": f"Импортировано {count} записей."})
        except Exception as error:
            message = "; ".join(error.messages) if isinstance(error, ValidationError) else str(error)
            return render(request, self.template_name, {"section": "import", "error": f"Ошибка импорта: {message}"})
