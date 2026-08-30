import csv
from collections import defaultdict
from datetime import timedelta, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Sum, Q, DecimalField, ExpressionWrapper
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.timezone import now, localdate

from .forms import (
    StockMovementForm, ExpenseForm, StaffCreateForm, StockTransferForm,
)
from .models import (
    MeatProduct, StockMovement, DailyStock, DailyBranchSummary, Butchery,
    Expense, ExpenseCategory, Staff, StockTransfer, AuditLog,
)
from .utils import send_low_stock_alert, log_action


def _total_stock_value():
    expr = ExpressionWrapper(
        F('current_stock') * F('buying_price'),
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )
    result = MeatProduct.objects.aggregate(total=Sum(expr))
    return result['total'] or Decimal('0.00')


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _paginate(request, queryset, per_page=20):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get('page'))


def _to_decimal(value):
    try:
        if value in (None, ''):
            return Decimal('0')
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def _opening_for(product, date):
    """Opening stock = the most recent closing stock recorded before `date`."""
    prev = (
        DailyStock.objects.filter(product=product, date__lt=date)
        .order_by('-date').first()
    )
    if prev:
        return prev.closing_stock
    return Decimal('0')


def _branch_day_stats(butchery, date):
    rows = DailyStock.objects.filter(
        product__butchery=butchery, date=date
    ).select_related('product')
    revenue = sum((r.revenue for r in rows), Decimal('0.00'))
    cogs = sum((r.cost_of_sales for r in rows), Decimal('0.00'))
    stock_value = sum((r.closing_value for r in rows), Decimal('0.00'))
    expenses = Expense.objects.filter(butchery=butchery, date=date).aggregate(
        t=Sum('amount'))['t'] or Decimal('0.00')
    summary = DailyBranchSummary.objects.filter(butchery=butchery, date=date).first()
    mpesa = summary.mpesa_amount if summary else Decimal('0.00')
    gross = revenue - cogs
    return {
        'butchery': butchery,
        'revenue': revenue,
        'cogs': cogs,
        'gross_profit': gross,
        'expenses': expenses,
        'net_profit': gross - expenses,
        'stock_value': stock_value,
        'entries': rows.count(),
        'mpesa_amount': mpesa,
        'cash_at_hand': revenue - mpesa,
    }


def _dashboard_context(date, branch_id=None):
    if branch_id:
        branch = Butchery.objects.filter(pk=branch_id).first()
        per_branch = [_branch_day_stats(branch, date)] if branch else []
    else:
        per_branch = [_branch_day_stats(b, date) for b in Butchery.objects.all()]
    keys = ['revenue', 'cogs', 'gross_profit', 'expenses', 'net_profit', 'stock_value', 'mpesa_amount', 'cash_at_hand']
    totals = {k: sum((b[k] for b in per_branch), Decimal('0.00')) for k in keys}
    return {'date': date, 'per_branch': per_branch, 'totals': totals, 'selected_branch_id': branch_id}


def login_view(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data.get('username'),
                password=form.cleaned_data.get('password'),
            )
            if user is not None:
                login(request, user)
                if user.is_superuser:
                    return redirect('home')
                return redirect('daily_stock_entry')
        messages.error(request, 'Invalid username or password.')
    else:
        form = AuthenticationForm()
    return render(request, 'login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def home_view(request):
    """Owner's live portfolio dashboard across all branches or a specific branch."""
    date = _parse_date(request.GET.get('date')) or localdate()
    branch_id = request.GET.get('branch_id')
    ctx = _dashboard_context(date, branch_id)
    ctx.update({
        'recent_activities': StockMovement.objects.select_related('product')[:8],
        'low_stock_count': sum(1 for p in MeatProduct.objects.all() if p.is_low_stock()),
        'date_str': date.isoformat(),
        'is_today': date == localdate(),
        'pending_transfers': StockTransfer.objects.filter(status='PENDING').count(),
        'branches': Butchery.objects.all(),
    }
    )
    return render(request, 'home.html', ctx)


@login_required
def dashboard_data(request):
    """JSON endpoint that powers the live auto-refreshing dashboard."""
    date = _parse_date(request.GET.get('date')) or localdate()
    branch_id = request.GET.get('branch_id')
    ctx = _dashboard_context(date, branch_id)
    return JsonResponse({
        'totals': {k: float(v) for k, v in ctx['totals'].items()},
        'per_branch': [
            {
                'name': b['butchery'].name,
                'revenue': float(b['revenue']),
                'gross_profit': float(b['gross_profit']),
                'expenses': float(b['expenses']),
                'net_profit': float(b['net_profit']),
                'stock_value': float(b['stock_value']),
                'entries': b['entries'],
                'mpesa_amount': float(b['mpesa_amount']),
                'cash_at_hand': float(b['cash_at_hand']),
            }
            for b in ctx['per_branch']
        ],
    })


@login_required
def stock_movement(request):
    if request.method == 'POST':
        form = StockMovementForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                movement = form.save(commit=False)
                movement.recorded_by = request.user
                product = movement.product
                if movement.movement_type == 'IN':
                    product.current_stock = F('current_stock') + movement.quantity
                elif movement.movement_type in ('OUT', 'WASTE'):
                    product.current_stock = F('current_stock') - movement.quantity
                product.save()
                movement.save()
                movement.product.refresh_from_db()
                if movement.product.is_low_stock():
                    send_low_stock_alert(movement.product)
                log_action(
                    request.user, 'STOCK_MOVEMENT', 'StockMovement', movement.pk,
                    f"{movement.movement_type} {movement.quantity} of {movement.product.name}",
                    request,
                )
            messages.success(request, 'Stock movement recorded successfully.')
            return redirect('home')
    else:
        form = StockMovementForm()
    return render(request, 'stock_movement.html', {'form': form})


@login_required
def daily_stock_entry(request):
    """Butcher records received + remaining stock and the day's expenses."""
    branches = Butchery.objects.all()
    locked_branch = None
    
    # Lock branch for non-superusers based on their staff assignment
    if not request.user.is_superuser:
        try:
            staff = request.user.staff_profile
            locked_branch = staff.butchery
        except Staff.DoesNotExist:
            # If no staff assignment, allow all branches (fallback)
            pass

    if request.method == 'POST':
        butchery = get_object_or_404(Butchery, pk=request.POST.get('butchery'))
        post_date = _parse_date(request.POST.get('date')) or localdate()
        with transaction.atomic():
            for product in butchery.products.all():
                received = _to_decimal(request.POST.get(f'received_{product.id}'))
                closing = _to_decimal(request.POST.get(f'closing_{product.id}'))
                wastage = _to_decimal(request.POST.get(f'wastage_{product.id}'))
                transfer_to_id = request.POST.get(f'transfer_to_{product.id}')
                transfer_quantity = _to_decimal(request.POST.get(f'transfer_quantity_{product.id}'))
                opening = _opening_for(product, post_date)
                
                # Handle transfer logic
                transfer_to = None
                if transfer_to_id and transfer_quantity > 0:
                    transfer_to = get_object_or_404(Butchery, pk=transfer_to_id)
                    # Validate that source has enough stock
                    if opening + received < transfer_quantity:
                        messages.error(request, f'Not enough stock for {product.name} to transfer {transfer_quantity} to {transfer_to.name}')
                        return redirect(f"{reverse('daily_stock_entry')}?butchery={butchery.id}&date={post_date}")
                    
                    # Find the same product in the destination branch
                    dest_product = MeatProduct.objects.filter(
                        name=product.name,
                        butchery=transfer_to,
                        category=product.category
                    ).first()
                    
                    if dest_product:
                        # Create incoming transfer for destination branch
                        dest_opening = _opening_for(dest_product, post_date)
                        dest_daily = DailyStock.objects.filter(product=dest_product, date=post_date).first()
                        dest_received = dest_daily.received if dest_daily else Decimal('0')
                        dest_closing = dest_daily.closing_stock if dest_daily else dest_opening
                        dest_wastage = dest_daily.wastage if dest_daily else Decimal('0')
                        
                        DailyStock.objects.update_or_create(
                            product=dest_product, date=post_date,
                            defaults={
                                'opening_stock': dest_opening,
                                'received': dest_received + transfer_quantity,
                                'closing_stock': dest_closing + transfer_quantity,
                                'wastage': dest_wastage,
                                'transfer_from': butchery,
                                'transfer_quantity': transfer_quantity,
                                'recorded_by': request.user,
                            },
                        )
                        # Update destination product's current stock
                        dest_product.current_stock = dest_closing + transfer_quantity
                        dest_product.save()
                        messages.success(request, f'Transferred {transfer_quantity} {product.name} to {transfer_to.name}')
                
                DailyStock.objects.update_or_create(
                    product=product, date=post_date,
                    defaults={
                        'opening_stock': opening,
                        'received': received,
                        'closing_stock': closing,
                        'wastage': wastage,
                        'transfer_to': transfer_to,
                        'transfer_quantity': transfer_quantity if transfer_to else Decimal('0'),
                        'recorded_by': request.user,
                    },
                )
                MeatProduct.objects.filter(pk=product.pk).update(current_stock=closing)
            for amount, desc in zip(
                request.POST.getlist('exp_amount'),
                request.POST.getlist('exp_description'),
            ):
                amt = _to_decimal(amount)
                if desc and amt > 0:
                    Expense.objects.create(
                        butchery=butchery, amount=amt,
                        description=desc, date=post_date, recorded_by=request.user,
                    )
            mpesa_amt = _to_decimal(request.POST.get('mpesa_amount') or 0)
            notes = request.POST.get('notes', '').strip()
            DailyBranchSummary.objects.update_or_create(
                butchery=butchery, date=post_date,
                defaults={'mpesa_amount': mpesa_amt, 'notes': notes, 'recorded_by': request.user},
            )
            log_action(
                request.user, 'DAILY_STOCK', 'DailyStock', 0,
                f"Daily entry for {butchery.name} on {post_date}", request,
            )
        messages.success(request, 'Daily stock and expenses saved successfully.')
        return redirect(f"{reverse('daily_stock_entry')}?butchery={butchery.id}&date={post_date}")

    date = _parse_date(request.GET.get('date')) or localdate()
    selected = (
        locked_branch
        or branches.filter(pk=request.GET.get('butchery')).first()
        or branches.first()
    )
    rows = []
    if selected:
        for product in selected.products.select_related('category'):
            existing = DailyStock.objects.filter(product=product, date=date).first()
            rows.append({
                'product': product,
                'opening': _opening_for(product, date),
                'received': existing.received if existing else '',
                'closing': existing.closing_stock if existing else '',
                'wastage': existing.wastage if existing else '',
                'transfer_quantity': existing.transfer_quantity if existing else '',
                'transfer_from': existing.transfer_from if existing else None,
                'transfer_to': existing.transfer_to if existing else None,
            })
    summary = DailyBranchSummary.objects.filter(butchery=selected, date=date).first()
    context = {
        'branches': branches,
        'locked_branch': locked_branch,
        'selected': selected,
        'rows': rows,
        'date': date.isoformat(),
        'mpesa_amount': summary.mpesa_amount if summary else '',
        'notes': summary.notes if summary else '',
    }
    return render(request, 'daily_stock_entry.html', context)


@login_required
def daily_stock_readonly(request):
    """Read-only view of daily entry for a specific branch (owner view)."""
    date = _parse_date(request.GET.get('date')) or localdate()
    branch_id = request.GET.get('butchery_id')
    branch = get_object_or_404(Butchery, pk=branch_id)
    
    rows = []
    for product in branch.products.select_related('category'):
        existing = DailyStock.objects.filter(product=product, date=date).first()
        rows.append({
            'product': product,
            'opening': _opening_for(product, date),
            'received': existing.received if existing else Decimal('0'),
            'closing': existing.closing_stock if existing else Decimal('0'),
            'wastage': existing.wastage if existing else Decimal('0'),
            'sold': existing.sold if existing else Decimal('0'),
            'revenue': existing.revenue if existing else Decimal('0'),
        })
    
    expenses = Expense.objects.filter(butchery=branch, date=date)
    summary = DailyBranchSummary.objects.filter(butchery=branch, date=date).first()
    
    context = {
        'branch': branch,
        'date': date,
        'rows': rows,
        'expenses': expenses,
        'mpesa_amount': summary.mpesa_amount if summary else Decimal('0'),
        'notes': summary.notes if summary else '',
        'total_revenue': sum(r['revenue'] for r in rows),
        'total_expenses': sum(e.amount for e in expenses),
    }
    return render(request, 'daily_stock_readonly.html', context)


@login_required
def daily_stock_history(request):
    qs = DailyStock.objects.select_related('product', 'product__butchery')
    q = request.GET.get('q', '').strip()
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    butchery_id = request.GET.get('butchery_id')
    if q:
        qs = qs.filter(product__name__icontains=q)
    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    if butchery_id:
        qs = qs.filter(product__butchery_id=butchery_id)
    return render(request, 'daily_stock_history.html', {
        'page_obj': _paginate(request, qs),
        'butcheries': Butchery.objects.all(),
        'q': q,
        'request': request,
    })


def _compute_profit_loss(butchery_id, start_date, end_date):
    qs = DailyStock.objects.select_related('product')
    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    if butchery_id:
        qs = qs.filter(product__butchery_id=butchery_id)

    prod = {}
    total_revenue = total_cogs = wastage_cost = Decimal('0.00')
    for ds in qs:
        row = prod.setdefault(ds.product.name, {
            'name': ds.product.name,
            'sold': Decimal('0'),
            'revenue': Decimal('0.00'),
            'cogs': Decimal('0.00'),
            'wastage': Decimal('0'),
        })
        row['sold'] += ds.sold
        row['revenue'] += ds.revenue
        row['cogs'] += ds.cost_of_sales
        row['wastage'] += ds.wastage
        total_revenue += ds.revenue
        total_cogs += ds.cost_of_sales
        wastage_cost += ds.wastage * ds.product.buying_price

    per_product = list(prod.values())
    for row in per_product:
        row['gross_profit'] = row['revenue'] - row['cogs']
    per_product.sort(key=lambda r: r['revenue'], reverse=True)

    expenses = Expense.objects.all()
    if start_date:
        expenses = expenses.filter(date__gte=start_date)
    if end_date:
        expenses = expenses.filter(date__lte=end_date)
    if butchery_id:
        expenses = expenses.filter(butchery_id=butchery_id)
    total_expenses = expenses.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    gross_profit = total_revenue - total_cogs
    return {
        'total_revenue': total_revenue,
        'total_cogs': total_cogs,
        'gross_profit': gross_profit,
        'total_expenses': total_expenses,
        'net_profit': gross_profit - total_expenses,
        'wastage_cost': wastage_cost,
        'per_product': per_product,
    }


@login_required
@permission_required('inventory.view_reports', raise_exception=True)
def reporting_dashboard(request):
    butchery_id = request.GET.get('butchery')
    start = _parse_date(request.GET.get('start_date')) or (localdate() - timedelta(days=7))
    end = _parse_date(request.GET.get('end_date')) or localdate()
    data = _compute_profit_loss(butchery_id, start, end)
    by_sold = sorted(data['per_product'], key=lambda r: r['sold'], reverse=True)
    low_stock_products = [
        p for p in MeatProduct.objects.select_related('category', 'butchery')
        if p.is_low_stock()
    ]
    context = {
        'low_stock_products': low_stock_products,
        'total_stock_value': _total_stock_value(),
        'recent_movements': StockMovement.objects.select_related('product')[:10],
        'best_sellers': by_sold[:5],
        'worst_sellers': list(reversed(by_sold))[:5],
        'butcheries': Butchery.objects.all(),
        'selected_butchery': butchery_id,
        'start_date': start.isoformat(),
        'end_date': end.isoformat(),
    }
    context.update(data)
    return render(request, 'reporting_dashboard.html', context)


@login_required
@permission_required('inventory.view_reports', raise_exception=True)
def export_reports(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="stock_report.csv"'
    writer = csv.writer(response)
    writer.writerow(['Branch', 'Product', 'Current Stock', 'Unit', 'Buying Price', 'Selling Price', 'Stock Value'])
    for p in MeatProduct.objects.select_related('butchery'):
        writer.writerow([
            p.butchery.name, p.name, p.current_stock, p.get_unit_display(),
            p.buying_price, p.selling_price, p.current_stock * p.buying_price,
        ])
    return response


# ===========================================================================
# Expenses
# ===========================================================================
@login_required
def expense_create(request):
    if request.method == 'POST':
        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                expense = form.save(commit=False)
                expense.recorded_by = request.user
                expense.save()
            messages.success(request, 'Expense recorded successfully.')
            return redirect('reporting_dashboard')
    else:
        form = ExpenseForm()
    return render(request, 'expense_create.html', {'form': form})


@login_required
def expense_list(request):
    qs = Expense.objects.select_related('butchery', 'category')
    q = request.GET.get('q', '').strip()
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    butchery_id = request.GET.get('butchery_id')
    if q:
        qs = qs.filter(Q(description__icontains=q) | Q(category__name__icontains=q))
    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    if butchery_id:
        qs = qs.filter(butchery_id=butchery_id)
    context = {
        'page_obj': _paginate(request, qs),
        'butcheries': Butchery.objects.all(),
        'q': q,
        'request': request,
    }
    return render(request, 'expense_list.html', context)


# ===========================================================================
# Staff management
# ===========================================================================
@login_required
@permission_required('inventory.manage_stock', raise_exception=True)
def staff_list(request):
    staff = Staff.objects.select_related('user', 'butchery').order_by('butchery__name', 'role')
    return render(request, 'staff_list.html', {'staff_members': staff})


@login_required
@permission_required('inventory.manage_stock', raise_exception=True)
def staff_create(request):
    if request.method == 'POST':
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                staff = form.save()
                log_action(
                    request.user, 'STAFF_CREATED', 'Staff', staff.pk,
                    f"Created staff {staff.user.username} ({staff.role})",
                    request,
                )
            messages.success(request, 'Staff member created successfully.')
            return redirect('staff_list')
    else:
        form = StaffCreateForm()
    return render(request, 'staff_create.html', {'form': form})


# ===========================================================================
# Profit & Loss
# ===========================================================================
@login_required
@permission_required('inventory.view_reports', raise_exception=True)
def profit_loss_report(request):
    butchery_id = request.GET.get('butchery')
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    if not start_date and not end_date:
        start_date = localdate() - timedelta(days=30)
        end_date = localdate()

    data = _compute_profit_loss(butchery_id, start_date, end_date)
    data.update({
        'butcheries': Butchery.objects.all(),
        'selected_butchery': butchery_id,
        'start_date': start_date.isoformat() if start_date else '',
        'end_date': end_date.isoformat() if end_date else '',
    })
    return render(request, 'profit_loss_report.html', data)


@login_required
@permission_required('inventory.view_reports', raise_exception=True)
def export_profit_loss(request):
    butchery_id = request.GET.get('butchery')
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    data = _compute_profit_loss(butchery_id, start_date, end_date)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="profit_loss.csv"'
    writer = csv.writer(response)
    writer.writerow(['Metric', 'Amount'])
    writer.writerow(['Total Revenue', data['total_revenue']])
    writer.writerow(['Total COGS', data['total_cogs']])
    writer.writerow(['Gross Profit', data['gross_profit']])
    writer.writerow(['Total Expenses', data['total_expenses']])
    writer.writerow(['Net Profit', data['net_profit']])
    writer.writerow(['Wastage Cost', data['wastage_cost']])
    writer.writerow([])
    writer.writerow(['Product', 'Qty Sold', 'Revenue', 'COGS', 'Gross Profit'])
    for row in data['per_product']:
        writer.writerow([row['name'], row['sold'], row['revenue'], row['cogs'], row['gross_profit']])
    return response


# ===========================================================================
# Stock transfers
# ===========================================================================
@login_required
def transfer_create(request):
    if request.method == 'POST':
        form = StockTransferForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                transfer = form.save(commit=False)
                transfer.requested_by = request.user
                transfer.status = 'PENDING'
                transfer.save()
            messages.success(request, 'Stock transfer requested successfully.')
            return redirect('home')
    else:
        form = StockTransferForm()
    return render(request, 'transfer_create.html', {'form': form})


@login_required
@permission_required('inventory.manage_stock', raise_exception=True)
def transfer_approve(request, pk):
    transfer = get_object_or_404(
        StockTransfer.objects.select_related('from_butchery', 'to_butchery', 'product'), pk=pk
    )
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            transfer.approve(request.user)
            log_action(
                request.user, 'TRANSFER_APPROVED', 'StockTransfer', transfer.pk,
                f"Approved transfer of {transfer.quantity} {transfer.product.name}",
                request,
            )
            messages.success(request, 'Transfer approved and stock moved.')
        elif action == 'reject':
            transfer.status = 'REJECTED'
            transfer.approved_by = request.user
            transfer.save()
            log_action(
                request.user, 'TRANSFER_REJECTED', 'StockTransfer', transfer.pk,
                f"Rejected transfer of {transfer.quantity} {transfer.product.name}",
                request,
            )
            messages.info(request, 'Transfer rejected.')
        return redirect('home')
    return render(request, 'transfer_approve.html', {'transfer': transfer})


# ===========================================================================
# Stock movement history (search + pagination)
# ===========================================================================
@login_required
def stock_movement_list(request):
    qs = StockMovement.objects.select_related('product', 'recorded_by')
    q = request.GET.get('q', '').strip()
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    butchery_id = request.GET.get('butchery_id')
    if q:
        qs = qs.filter(product__name__icontains=q)
    if start_date:
        qs = qs.filter(timestamp__date__gte=start_date)
    if end_date:
        qs = qs.filter(timestamp__date__lte=end_date)
    if butchery_id:
        qs = qs.filter(product__butchery_id=butchery_id)
    return render(request, 'stock_movement_list.html', {
        'page_obj': _paginate(request, qs),
        'butcheries': Butchery.objects.all(),
        'q': q,
        'request': request,
    })
