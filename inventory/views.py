import csv
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView, View
from django.db.models import Q, Sum, F, Value, CharField
from django.db.models.functions import Coalesce
from .models import (
    Equipment, Consumable, Receipt, Disposal, WriteOff, Order, Movement,
    EquipmentType, Location, Supplier, DisposalDirection, Operator,
)
from .forms import (
    EquipmentForm, ConsumableForm, ReceiptForm, DisposalForm, WriteOffForm,
    OrderForm, MovementForm,
)


def user_login(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next", "/")
            return redirect(next_url)
        return render(request, "inventory/login.html", {"error": "Неверное имя пользователя или пароль"})
    return render(request, "inventory/login.html")


def user_logout(request):
    logout(request)
    return redirect("login")


def can_edit(user):
    return user.is_authenticated and (user.is_staff or user.has_perm("inventory.change_equipment"))


def can_delete(user):
    return user.is_authenticated and user.is_staff


class StaffRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class EditorRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (request.user.is_staff or request.user.has_perm("inventory.change_equipment")):
            return redirect("/")
        return super().dispatch(request, *args, **kwargs)


def get_sidebar_context(request):
    return {"current_user": request.user}


# ─── Dashboard ───────────────────────────────────────────────────────────────

@login_required
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

class EquipmentListView(LoginRequiredMixin, ListView):
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
        if eq_type:
            qs = qs.filter(equipment_type_id=eq_type)
        loc = self.request.GET.get("location", "")
        if loc:
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


class EquipmentDetailView(LoginRequiredMixin, DetailView):
    model = Equipment
    template_name = "inventory/equipment_detail.html"
    context_object_name = "eq"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "equipment"
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
def generate_inventory_number(request):
    if request.method == "GET":
        inv_num = Equipment.generate_inventory_number()
        return JsonResponse({"inventory_number": inv_num})


# ─── Consumables ─────────────────────────────────────────────────────────────

class ConsumableListView(LoginRequiredMixin, ListView):
    model = Consumable
    template_name = "inventory/consumable_list.html"
    context_object_name = "consumable_list"
    paginate_by = 25

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

class ReceiptListView(LoginRequiredMixin, ListView):
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
        response = super().form_valid(form)
        self._update_consumable(form)
        return response

    def _update_consumable(self, form):
        eq_type = form.cleaned_data["equipment_type"]
        name = form.cleaned_data["name"]
        qty = form.cleaned_data["quantity"]
        cons, created = Consumable.objects.get_or_create(
            equipment_type=eq_type, name=name,
            defaults={"quantity": 0},
        )
        if created:
            cons.quantity = qty
        else:
            cons.quantity += qty
        cons.save(update_fields=["quantity"])


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

class DisposalListView(LoginRequiredMixin, ListView):
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
        response = super().form_valid(form)
        self._apply_changes(form)
        return response

    def _apply_changes(self, form):
        link_eq = form.cleaned_data.get("link_equipment")
        link_cons = form.cleaned_data.get("link_consumable")
        qty = form.cleaned_data["quantity"]

        if link_eq:
            # Update equipment: change status to "broken" or set location to direction
            link_eq.status = "broken"
            if form.cleaned_data.get("direction"):
                # Try to find location matching direction name
                loc, _ = Location.objects.get_or_create(name=str(form.cleaned_data["direction"]))
                link_eq.location = loc
            link_eq.save(update_fields=["status", "location"])
        elif link_cons:
            # Deduct from consumable quantity
            link_cons.quantity -= qty
            if link_cons.quantity < 0:
                link_cons.quantity = 0
            link_cons.save(update_fields=["quantity"])
        else:
            # Fallback: original behavior — deduct from matching consumable
            try:
                cons = Consumable.objects.get(
                    equipment_type=form.cleaned_data["equipment_type"],
                    name=form.cleaned_data["name"]
                )
                cons.quantity -= qty
                if cons.quantity < 0:
                    cons.quantity = 0
                cons.save(update_fields=["quantity"])
            except Consumable.DoesNotExist:
                pass


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

class WriteOffListView(LoginRequiredMixin, ListView):
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
        return ctx


class EquipmentTypeListView(LoginRequiredMixin, RefMixin, ListView):
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


class LocationListView(LoginRequiredMixin, RefMixin, ListView):
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


class SupplierListView(LoginRequiredMixin, RefMixin, ListView):
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


class DisposalDirectionListView(LoginRequiredMixin, RefMixin, ListView):
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


class OperatorListView(LoginRequiredMixin, RefMixin, ListView):
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

class OrderListView(LoginRequiredMixin, ListView):
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


# ─── Movements ─────────────────────────────────────────────────────────────

class MovementListView(LoginRequiredMixin, ListView):
    model = Movement
    template_name = "inventory/movement_list.html"
    context_object_name = "movement_list"
    paginate_by = 25

    def get_queryset(self):
        qs = Movement.objects.select_related("equipment", "consumable", "from_location", "to_location", "from_operator", "to_operator", "created_by")
        q = self.request.GET.get("q", "")
        if q:
            qs = qs.filter(
                Q(note__icontains=q) |
                Q(equipment__name__icontains=q) |
                Q(consumable__name__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["section"] = "movements"
        return ctx


class MovementCreateView(EditorRequiredMixin, CreateView):
    model = Movement
    form_class = MovementForm
    template_name = "inventory/movement_form.html"
    success_url = reverse_lazy("movement_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "movements"
        return ctx

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        # Update equipment location/operator
        eq = form.cleaned_data.get("equipment")
        if eq:
            if form.cleaned_data.get("to_location"):
                eq.location = form.cleaned_data["to_location"]
            if form.cleaned_data.get("to_operator"):
                eq.operator = form.cleaned_data["to_operator"]
            eq.save(update_fields=["location", "operator"])
        # Update consumable location
        cons = form.cleaned_data.get("consumable")
        if cons and form.cleaned_data.get("to_location"):
            cons.location = form.cleaned_data["to_location"]
            cons.save(update_fields=["location"])
        return response


class MovementUpdateView(EditorRequiredMixin, UpdateView):
    model = Movement
    form_class = MovementForm
    template_name = "inventory/movement_form.html"
    success_url = reverse_lazy("movement_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "movements"
        return ctx


class MovementDeleteView(StaffRequiredMixin, DeleteView):
    model = Movement
    template_name = "inventory/confirm_delete.html"
    success_url = reverse_lazy("movement_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(get_sidebar_context(self.request))
        ctx["section"] = "movements"
        return ctx


# ─── Export ─────────────────────────────────────────────────────────────────

class ExportCSVView(LoginRequiredMixin, View):
    def get(self, request, model_name):
        model_map = {
            "equipment": (Equipment, ["inventory_number", "name", "equipment_type__name", "status", "location__name", "operator__full_name", "description"]),
            "consumable": (Consumable, ["name", "equipment_type__name", "quantity", "location__name"]),
            "receipt": (Receipt, ["date", "name", "equipment_type__name", "status", "quantity", "supplier__name"]),
            "disposal": (Disposal, ["date", "name", "equipment_type__name", "quantity", "direction__name"]),
            "writeoff": (WriteOff, ["name", "object_type", "reason", "status", "actual_date", "final_date"]),
            "order": (Order, ["branch", "name", "equipment_type__name", "quantity", "status", "created_at"]),
            "movement": (Movement, ["equipment__name", "consumable__name", "from_location__name", "to_location__name", "from_operator__full_name", "to_operator__full_name", "created_at"]),
        }
        if model_name not in model_map:
            return JsonResponse({"error": "unknown model"}, status=404)
        model_class, fields = model_map[model_name]
        qs = model_class.objects.all()
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{model_name}.csv"'
        response.write("\ufeff".encode("utf-8"))
        writer = csv.writer(response)
        headers = [f.split("__")[-1] for f in fields]
        writer.writerow(headers)
        for obj in qs:
            row = []
            for f in fields:
                parts = f.split("__")
                val = obj
                for p in parts:
                    val = getattr(val, p, "") if val else ""
                row.append(str(val) if val is not None else "")
            writer.writerow(row)
        return response
