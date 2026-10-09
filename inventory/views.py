import csv
from collections import defaultdict
from datetime import timedelta, datetime, time
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Min, Sum, Q, DecimalField, ExpressionWrapper
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.timezone import now, localdate, localtime
from django.views.decorators.http import require_POST

from .forms import (
    StockMovementForm, ExpenseForm, StaffCreateForm, StockTransferForm,
)
from .models import (
    MeatProduct, StockMovement, DailyStock, DailyBranchSummary, Butchery,
    Expense, ExpenseCategory, Staff, StockTransfer, AuditLog, DatePermission, BuyingPrice, DailyTransfer,
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


MAX_AMOUNT = Decimal('99999999.99')


def _parse_amount(value):
    """Parse a non-negative amount. Blank means 0; invalid or negative returns None."""
    if value is None or str(value).strip() == '':
        return Decimal('0')
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0 or amount > MAX_AMOUNT:
        return None
    return amount.quantize(Decimal('0.01'))


def _low_stock_qs():
    return MeatProduct.objects.filter(current_stock__lte=F('minimum_stock'))


def _business_date():
    """Current business day; entries before BUSINESS_DAY_CUTOFF_HOUR count for the previous day."""
    current = localtime()
    if current.hour < settings.BUSINESS_DAY_CUTOFF_HOUR:
        return current.date() - timedelta(days=1)
    return current.date()


def _opening_for(product, date):
    """Opening stock = the most recent closing stock recorded before `date`."""
    prev = (
        DailyStock.objects.filter(product=product, date__lt=date)
        .order_by('-date').first()
    )
    if prev:
        return prev.closing_stock
    return Decimal('0')


def _carry_forward(product, saved_row):
    """Push a day's closing stock into later days' opening stock and sync current stock."""
    prev_closing = saved_row.closing_stock
    for row in DailyStock.objects.filter(product=product, date__gt=saved_row.date).order_by('date'):
        if row.opening_stock != prev_closing:
            row.opening_stock = prev_closing
            row.save(update_fields=['opening_stock'])
        prev_closing = row.closing_stock
    MeatProduct.objects.filter(pk=product.pk).update(current_stock=prev_closing)


def _locked_branch(user):
    """Branch a non-owner is assigned to, or None."""
    if user.is_superuser:
        return None
    staff = getattr(user, 'staff_profile', None)
    return staff.butchery if staff else None


def _has_date_permission(user, butchery, date):
    return DatePermission.objects.filter(
        user=user, butchery=butchery, start_date__lte=date, end_date__gte=date, is_active=True,
    ).exists()


def _first_missing_day(butchery):
    """Oldest past business day the branch left empty (no entry, not closed), or None.

    Only days from MISSING_DAY_ENFORCE_FROM and from the branch's first ever entry count.
    """
    today = _business_date()
    firsts = [
        DailyStock.objects.filter(product__butchery=butchery).aggregate(d=Min('date'))['d'],
        DailyBranchSummary.objects.filter(butchery=butchery).aggregate(d=Min('date'))['d'],
    ]
    firsts = [d for d in firsts if d]
    if not firsts:
        return None
    start = max(settings.MISSING_DAY_ENFORCE_FROM, min(firsts))
    if start >= today:
        return None
    entered = set(
        DailyStock.objects.filter(product__butchery=butchery, date__range=(start, today))
        .values_list('date', flat=True)
    ) | set(
        DailyBranchSummary.objects.filter(butchery=butchery, date__range=(start, today))
        .values_list('date', flat=True)
    )
    day = start
    while day < today:
        if day not in entered:
            return day
        day += timedelta(days=1)
    return None


def _edit_block_reason(user, butchery, date, summary):
    """Return why `user` may not edit this branch/day, or '' if they may."""
    business_day = _business_date()
    if date > business_day:
        return 'You cannot record stock for a future date.'
    if user.is_superuser or _has_date_permission(user, butchery, date):
        return ''
    if summary and summary.is_closed:
        return 'This day has been closed by the owner and can no longer be edited.'
    missing = _first_missing_day(butchery)
    if missing:
        if date == missing:
            return ''
        return f"No data was entered for {missing:%a %d %b}. Fill in that day first."
    if date != business_day:
        return 'You can only edit data for today. Contact the admin to make changes to past dates.'
    return ''


def _parse_expense_rows(post, categories):
    ids = post.getlist('exp_id')
    amounts = post.getlist('exp_amount')
    cats = post.getlist('exp_category')
    rows, errors = [], []
    for i, desc in enumerate(post.getlist('exp_description')):
        desc = desc.strip()
        raw_amount = amounts[i] if i < len(amounts) else ''
        raw_id = ids[i] if i < len(ids) else ''
        raw_cat = cats[i] if i < len(cats) else ''
        amount = _parse_amount(raw_amount)
        if not desc and amount == 0:
            continue
        if not desc:
            errors.append('Each expense needs a description.')
        elif not amount:
            errors.append(f'Expense "{desc}": enter an amount greater than zero.')
        rows.append({
            'id': int(raw_id) if raw_id.isdigit() else None,
            'description': desc,
            'amount': raw_amount,
            'amount_value': amount,
            'category_id': raw_cat,
            'category': categories.get(int(raw_cat)) if raw_cat.isdigit() else None,
        })
    return rows, errors


def _parse_transfer_rows(post, product, branch_ids):
    ids = post.getlist(f'transfer_id_{product.id}')
    targets = post.getlist(f'transfer_to_{product.id}')
    quantities = post.getlist(f'transfer_quantity_{product.id}')
    rows, errors = [], []
    for i, target in enumerate(targets):
        target = target.strip()
        raw_qty = quantities[i] if i < len(quantities) else ''
        raw_id = ids[i] if i < len(ids) else ''
        if not target and not raw_qty.strip():
            continue
        qty = _parse_amount(raw_qty)
        if not target.isdigit() or int(target) not in branch_ids:
            errors.append(f'{product.name}: choose the branch the transfer went to.')
        elif not qty:
            errors.append(f'{product.name}: enter a transfer quantity greater than zero.')
        rows.append({
            'id': int(raw_id) if raw_id.isdigit() else None,
            'to_id': target,
            'quantity': raw_qty,
            'quantity_value': qty,
        })
    return rows, errors


def _sync_rows(existing, rows, update, create):
    """Update rows whose id is in `existing`, create the rest, delete existing rows not submitted."""
    kept = set()
    for row in rows:
        obj = existing.get(row['id'])
        if obj:
            update(obj, row)
            kept.add(obj.pk)
        else:
            create(row)
    for pk, obj in existing.items():
        if pk not in kept:
            obj.delete()


def _save_expenses(butchery, date, rows, user, replace):
    existing = {e.pk: e for e in Expense.objects.filter(butchery=butchery, date=date)} if replace else {}

    def update(exp, row):
        exp.description = row['description']
        exp.amount = row['amount_value']
        exp.category = row['category']
        exp.save(update_fields=['description', 'amount', 'category'])

    def create(row):
        Expense.objects.create(
            butchery=butchery, category=row['category'], amount=row['amount_value'],
            description=row['description'], date=date, recorded_by=user,
        )
    _sync_rows(existing, rows, update, create)


def _save_transfers(daily_stock, rows, replace):
    existing = {t.pk: t for t in daily_stock.transfers_out.all()} if replace else {}

    def update(transfer, row):
        transfer.to_butchery_id = int(row['to_id'])
        transfer.quantity = row['quantity_value']
        transfer.save(update_fields=['to_butchery', 'quantity'])

    def create(row):
        DailyTransfer.objects.create(
            source_daily_stock=daily_stock, to_butchery_id=int(row['to_id']),
            quantity=row['quantity_value'],
        )
    _sync_rows(existing, rows, update, create)


def _branch_day_stats(butchery, date):
    rows = list(
        DailyStock.objects.filter(product__butchery=butchery, date=date)
        .select_related('product').prefetch_related('transfers_out')
    )
    revenue = sum((r.revenue for r in rows), Decimal('0.00'))
    cogs = sum((r.cost_of_sales for r in rows), Decimal('0.00'))
    stock_value = sum((r.closing_value for r in rows), Decimal('0.00'))
    expenses = Expense.objects.filter(butchery=butchery, date=date).aggregate(
        t=Sum('amount'))['t'] or Decimal('0.00')
    summary = DailyBranchSummary.objects.filter(butchery=butchery, date=date).first()
    mpesa = summary.mpesa_amount if summary else Decimal('0.00')
    gross = revenue - cogs
    expected_cash = revenue - mpesa - expenses
    cash_counted = summary.cash_counted if summary else None
    return {
        'butchery': butchery,
        'revenue': revenue,
        'cogs': cogs,
        'gross_profit': gross,
        'expenses': expenses,
        'net_profit': gross - expenses,
        'stock_value': stock_value,
        'entries': len(rows),
        'mpesa_amount': mpesa,
        'expected_cash': expected_cash,
        'cash_counted': cash_counted,
        'cash_variance': (cash_counted - expected_cash) if cash_counted is not None else None,
        'submitted': summary is not None,
        'is_closed': bool(summary and summary.is_closed),
    }


def _dashboard_context(date, branch_id=None):
    if branch_id:
        branch = Butchery.objects.filter(pk=branch_id).first() if str(branch_id).isdigit() else None
        per_branch = [_branch_day_stats(branch, date)] if branch else []
    else:
        per_branch = [_branch_day_stats(b, date) for b in Butchery.objects.all()]
    keys = ['revenue', 'cogs', 'gross_profit', 'expenses', 'net_profit', 'stock_value', 'mpesa_amount', 'expected_cash']
    totals = {k: sum((b[k] for b in per_branch), Decimal('0.00')) for k in keys}
    totals['cash_variance'] = sum(
        (b['cash_variance'] for b in per_branch if b['cash_variance'] is not None), Decimal('0.00')
    )
    return {'date': date, 'per_branch': per_branch, 'totals': totals, 'selected_branch_id': branch_id}


MISSING_RANGE_CHOICES = (7, 14, 30)


def _entry_status_grid(days):
    """Per-branch daily entry status for the last `days` business days, newest first.

    Statuses: submitted, closed, missing, today (current day not entered yet),
    none (before the branch's first ever entry).
    """
    today = _business_date()
    start = today - timedelta(days=days - 1)
    dates = [today - timedelta(days=i) for i in range(days)]

    closed = {}
    for b_id, d, is_closed in DailyBranchSummary.objects.filter(
        date__range=(start, today)
    ).values_list('butchery_id', 'date', 'is_closed'):
        closed[(b_id, d)] = is_closed
    entered = set(
        DailyStock.objects.filter(date__range=(start, today))
        .values_list('product__butchery_id', 'date').distinct()
    ) | set(closed)

    first_entry = {}
    for model, field in ((DailyStock, 'product__butchery_id'), (DailyBranchSummary, 'butchery_id')):
        for b_id, first in model.objects.values(field).annotate(first=Min('date')).values_list(field, 'first'):
            if b_id not in first_entry or first < first_entry[b_id]:
                first_entry[b_id] = first

    rows = []
    for branch in Butchery.objects.order_by('name'):
        first = first_entry.get(branch.id)
        cells, missing = [], 0
        for d in dates:
            if (branch.id, d) in entered:
                status = 'closed' if closed.get((branch.id, d)) else 'submitted'
            elif first is None or d < first:
                status = 'none'
            elif d == today:
                status = 'today'
            else:
                status = 'missing'
                missing += 1
            cells.append({'date': d, 'status': status})
        rows.append({'branch': branch, 'cells': cells, 'missing': missing, 'never_entered': first is None})
    return {
        'status_dates': dates,
        'status_rows': rows,
        'missing_total': sum(r['missing'] for r in rows),
    }


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
    missing_days = request.GET.get('missing_days', '')
    missing_days = int(missing_days) if missing_days.isdigit() and int(missing_days) in MISSING_RANGE_CHOICES else 14
    ctx = _dashboard_context(date, branch_id)
    ctx.update({
        'recent_activities': StockMovement.objects.select_related('product')[:8],
        'low_stock_count': _low_stock_qs().count(),
        'date_str': date.isoformat(),
        'is_today': date == localdate(),
        'pending_transfers': StockTransfer.objects.filter(status='PENDING').count(),
        'branches': Butchery.objects.all(),
        'missing_days': missing_days,
        'missing_range_choices': MISSING_RANGE_CHOICES,
        **_entry_status_grid(missing_days),
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
                'id': b['butchery'].id,
                'name': b['butchery'].name,
                'revenue': float(b['revenue']),
                'gross_profit': float(b['gross_profit']),
                'expenses': float(b['expenses']),
                'net_profit': float(b['net_profit']),
                'stock_value': float(b['stock_value']),
                'entries': b['entries'],
                'mpesa_amount': float(b['mpesa_amount']),
                'expected_cash': float(b['expected_cash']),
                'cash_variance': None if b['cash_variance'] is None else float(b['cash_variance']),
                'submitted': b['submitted'],
                'is_closed': b['is_closed'],
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


def _entry_context(user, branches, selected, locked, date, posted=None, posted_expenses=None, posted_transfers=None):
    """Build the daily entry form context, from saved data or from a rejected submission."""
    summary = None
    rows, expenses = [], []
    if selected:
        summary = (
            DailyBranchSummary.objects.select_related('recorded_by', 'closed_by')
            .filter(butchery=selected, date=date).first()
        )
        saved = {
            d.product_id: d for d in DailyStock.objects.filter(product__butchery=selected, date=date)
            .prefetch_related('transfers_out')
        }
        incoming = DailyTransfer.objects.filter(
            to_butchery=selected, source_daily_stock__date=date,
        ).select_related('source_daily_stock__product__butchery')
        incoming_by_name = {}
        for t in incoming:
            incoming_by_name.setdefault(t.source_daily_stock.product.name, []).append(t)

        for product in selected.products.select_related('category'):
            ds = saved.get(product.id)
            if posted is not None:
                values = {f: posted.get(f'{f}_{product.id}', '') for f in ('received', 'wastage', 'closing')}
                transfers = posted_transfers.get(product.id, [])
            else:
                values = {
                    'received': ds.received if ds else '',
                    'wastage': ds.wastage if ds else '',
                    'closing': ds.closing_stock if ds else '',
                }
                transfers = [
                    {'id': t.pk, 'to_id': str(t.to_butchery_id), 'quantity': t.quantity}
                    for t in (ds.transfers_out.all() if ds else [])
                ]
            rows.append({
                'product': product,
                'opening': _opening_for(product, date),
                'selling_price': ds.unit_selling_price if ds else product.selling_price,
                'transfers': transfers,
                'transfers_in': incoming_by_name.get(product.name, []),
                **values,
            })
        if posted_expenses is not None:
            expenses = posted_expenses
        else:
            expenses = [
                {'id': e.pk, 'description': e.description, 'amount': e.amount,
                 'category_id': str(e.category_id or '')}
                for e in Expense.objects.filter(butchery=selected, date=date).order_by('pk')
            ]

    if posted is not None:
        mpesa, cash_counted, notes = (
            posted.get('mpesa_amount', ''), posted.get('cash_counted', ''), posted.get('notes', '')
        )
    else:
        mpesa = summary.mpesa_amount if summary else ''
        cash_counted = summary.cash_counted if summary and summary.cash_counted is not None else ''
        notes = summary.notes if summary else ''

    block_reason = _edit_block_reason(user, selected, date, summary) if selected else ''
    missing_day = _first_missing_day(selected) if selected else None
    return {
        'branches': branches,
        'other_branches': [b for b in branches if not selected or b.id != selected.id],
        'locked_branch': locked,
        'selected': selected,
        'rows': rows,
        'expenses': expenses,
        'categories': ExpenseCategory.objects.all(),
        'date': date.isoformat(),
        'today': _business_date().isoformat(),
        'mpesa_amount': mpesa,
        'cash_counted': cash_counted,
        'notes': notes,
        'summary': summary,
        'can_edit': not block_reason,
        'block_reason': block_reason,
        'missing_day': missing_day,
        'catching_up': missing_day is not None and missing_day == date,
    }


@login_required
def daily_stock_entry(request):
    """Butcher records received + remaining stock, transfers and the day's expenses."""
    branches = list(Butchery.objects.all())
    locked = _locked_branch(request.user)

    if request.method == 'POST':
        if locked:
            butchery = locked
        else:
            raw_id = request.POST.get('butchery', '')
            butchery = Butchery.objects.filter(pk=raw_id).first() if raw_id.isdigit() else None
        if butchery is None:
            raise Http404('Branch not found')
        post_date = _parse_date(request.POST.get('date')) or _business_date()
        redirect_url = f"{reverse('daily_stock_entry')}?butchery={butchery.id}&date={post_date}"

        summary = DailyBranchSummary.objects.filter(butchery=butchery, date=post_date).first()
        block_reason = _edit_block_reason(request.user, butchery, post_date, summary)
        if block_reason:
            messages.error(request, block_reason)
            return redirect(redirect_url)

        errors = []
        products = list(butchery.products.all())
        other_ids = {b.id for b in branches if b.id != butchery.id}
        quantities, transfers = {}, {}
        for product in products:
            values = {
                f: _parse_amount(request.POST.get(f'{f}_{product.id}'))
                for f in ('received', 'wastage', 'closing')
            }
            if None in values.values():
                errors.append(f'{product.name}: quantities must be zero or a positive number.')
            quantities[product.pk] = values
            rows, transfer_errors = _parse_transfer_rows(request.POST, product, other_ids)
            errors.extend(transfer_errors)
            transfers[product.pk] = rows
            if None not in values.values() and not transfer_errors:
                total_out = sum((r['quantity_value'] for r in rows), Decimal('0'))
                if total_out > _opening_for(product, post_date) + values['received']:
                    errors.append(f'{product.name}: transfers are more than the stock available.')

        categories = {c.pk: c for c in ExpenseCategory.objects.all()}
        expenses, expense_errors = _parse_expense_rows(request.POST, categories)
        errors.extend(expense_errors)

        mpesa = _parse_amount(request.POST.get('mpesa_amount'))
        if mpesa is None:
            errors.append('M-Pesa amount must be zero or a positive number.')
        raw_cash = request.POST.get('cash_counted', '').strip()
        cash_counted = _parse_amount(raw_cash) if raw_cash else None
        if raw_cash and cash_counted is None:
            errors.append('Cash handed over must be zero or a positive number.')

        if errors:
            for error in errors:
                messages.error(request, error)
            ctx = _entry_context(
                request.user, branches, butchery, locked, post_date,
                request.POST, expenses, transfers,
            )
            return render(request, 'daily_stock_entry.html', ctx, status=400)

        # Old open browser tabs don't send row ids, so only append for them.
        replace = request.POST.get('form_version') == '2'
        shortfalls = []
        with transaction.atomic():
            for product in products:
                q = quantities[product.pk]
                defaults = {
                    'opening_stock': _opening_for(product, post_date),
                    'received': q['received'],
                    'closing_stock': q['closing'],
                    'wastage': q['wastage'],
                    'recorded_by': request.user,
                }
                existing = DailyStock.objects.filter(product=product, date=post_date).first()
                # Keep the original prices when correcting a past day.
                if existing is None or existing.selling_price is None or post_date == _business_date():
                    defaults['buying_price'] = product.buying_price
                    defaults['selling_price'] = product.selling_price
                row, _ = DailyStock.objects.update_or_create(
                    product=product, date=post_date, defaults=defaults,
                )
                _save_transfers(row, transfers[product.pk], replace)
                if row.shortfall > 0:
                    shortfalls.append(product.name)
                _carry_forward(product, row)

            _save_expenses(butchery, post_date, expenses, request.user, replace)
            summary, _ = DailyBranchSummary.objects.update_or_create(
                butchery=butchery, date=post_date,
                defaults={
                    'mpesa_amount': mpesa,
                    'cash_counted': cash_counted,
                    'notes': request.POST.get('notes', '').strip(),
                    'recorded_by': request.user,
                },
            )
            log_action(
                request.user, 'DAILY_STOCK', 'DailyBranchSummary', summary.pk,
                f"Daily entry for {butchery.name} on {post_date}", request,
            )

        messages.success(request, 'Daily stock and expenses saved successfully.')
        if shortfalls:
            messages.warning(
                request,
                'Remaining stock is more than what was available for: '
                + ', '.join(shortfalls) + '. Please re-check the counts.',
            )
        if not request.user.is_superuser and post_date != _business_date():
            next_day = _first_missing_day(butchery) or _business_date()
            redirect_url = f"{reverse('daily_stock_entry')}?butchery={butchery.id}&date={next_day}"
        return redirect(redirect_url)

    date = _parse_date(request.GET.get('date')) or _business_date()
    selected = locked
    if selected is None:
        raw_id = request.GET.get('butchery', '')
        selected = next((b for b in branches if str(b.id) == raw_id), None) or (branches[0] if branches else None)
    if selected and not request.user.is_superuser:
        missing = _first_missing_day(selected)
        if missing and date != missing and not _has_date_permission(request.user, selected, date):
            messages.warning(
                request,
                f"No data was entered for {missing:%A %d %B}. Fill it in before you can record other days.",
            )
            return redirect(f"{reverse('daily_stock_entry')}?butchery={selected.id}&date={missing}")
    ctx = _entry_context(request.user, branches, selected, locked, date)
    return render(request, 'daily_stock_entry.html', ctx)


@login_required
def daily_stock_readonly(request):
    """Read-only view of daily entry for a specific branch (owner view)."""
    date = _parse_date(request.GET.get('date')) or _business_date()
    branch_id = request.GET.get('butchery_id', '')
    if not branch_id.isdigit():
        raise Http404('Branch not found')
    branch = get_object_or_404(Butchery, pk=branch_id)

    saved = {
        d.product_id: d for d in DailyStock.objects.filter(product__butchery=branch, date=date)
        .prefetch_related('transfers_out__to_butchery')
    }
    zero = Decimal('0')
    rows = []
    for product in branch.products.select_related('category'):
        ds = saved.get(product.id)
        rows.append({
            'product': product,
            'opening': ds.opening_stock if ds else _opening_for(product, date),
            'received': ds.received if ds else zero,
            'transferred_out': ds.transferred_out if ds else zero,
            'closing': ds.closing_stock if ds else zero,
            'wastage': ds.wastage if ds else zero,
            'sold': ds.sold if ds else zero,
            'revenue': ds.revenue if ds else zero,
            'shortfall': ds.shortfall if ds else zero,
            'transfers_out': list(ds.transfers_out.all()) if ds else [],
        })

    summary = (
        DailyBranchSummary.objects.select_related('recorded_by', 'closed_by')
        .filter(butchery=branch, date=date).first()
    )
    context = {
        'branch': branch,
        'date': date,
        'rows': rows,
        'transfers_in': DailyTransfer.objects.filter(
            to_butchery=branch, source_daily_stock__date=date,
        ).select_related('source_daily_stock__product__butchery'),
        'expenses': Expense.objects.filter(butchery=branch, date=date).select_related('category'),
        'summary': summary,
        'stats': _branch_day_stats(branch, date),
    }
    return render(request, 'daily_stock_readonly.html', context)


@login_required
@require_POST
def daily_close(request):
    """Owner locks (or unlocks) a branch's day so staff can no longer edit it."""
    if not request.user.is_superuser:
        raise PermissionDenied
    branch_id = request.POST.get('butchery_id', '')
    date = _parse_date(request.POST.get('date'))
    if not branch_id.isdigit() or date is None:
        return HttpResponseBadRequest('Invalid branch or date')
    branch = get_object_or_404(Butchery, pk=branch_id)
    close = request.POST.get('action') == 'close'

    summary, _ = DailyBranchSummary.objects.get_or_create(butchery=branch, date=date)
    summary.is_closed = close
    summary.closed_by = request.user if close else None
    summary.closed_at = now() if close else None
    summary.save(update_fields=['is_closed', 'closed_by', 'closed_at'])
    log_action(
        request.user, 'DAY_CLOSED' if close else 'DAY_REOPENED', 'DailyBranchSummary',
        summary.pk, f"{branch.name} on {date}", request,
    )
    messages.success(request, f"{branch.name} {date} {'closed' if close else 'reopened'}.")
    return redirect(f"{reverse('daily_stock_readonly')}?butchery_id={branch.id}&date={date}")


@login_required
def audit_log_list(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    qs = AuditLog.objects.select_related('user')
    q = request.GET.get('q', '').strip()
    action = request.GET.get('action', '')
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    if q:
        qs = qs.filter(Q(user__username__icontains=q) | Q(description__icontains=q))
    if action:
        qs = qs.filter(action=action)
    if start_date:
        qs = qs.filter(timestamp__date__gte=start_date)
    if end_date:
        qs = qs.filter(timestamp__date__lte=end_date)
    return render(request, 'audit_log_list.html', {
        'page_obj': _paginate(request, qs, per_page=50),
        'actions': AuditLog.objects.order_by('action').values_list('action', flat=True).distinct(),
        'q': q,
        'selected_action': action,
    })


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


@login_required
def buying_prices(request):
    """Owner-only view to manage buying prices."""
    if not request.user.is_superuser:
        messages.error(request, 'Only the owner can access this section.')
        return redirect('home')
    
    date = _parse_date(request.GET.get('date')) or localdate()
    products = MeatProduct.objects.select_related('category').all()
    
    # Get existing buying prices for the selected date as a dictionary
    existing_prices = {
        bp.product_id: bp for bp in BuyingPrice.objects.filter(date=date).select_related('product')
    }
    
    context = {
        'date': date,
        'products': products,
        'existing_prices': existing_prices,
    }
    return render(request, 'buying_prices.html', context)


@login_required
def save_buying_prices(request):
    """Owner-only view to save buying prices."""
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)
    
    date = _parse_date(request.POST.get('date'))
    if not date:
        return JsonResponse({'success': False, 'error': 'Invalid date'}, status=400)
    
    with transaction.atomic():
        for product in MeatProduct.objects.all():
            price_key = f'price_{product.id}'
            qty_key = f'quantity_{product.id}'
            notes_key = f'notes_{product.id}'
            
            buying_price = request.POST.get(price_key)
            quantity = request.POST.get(qty_key)
            notes = request.POST.get(notes_key, '')
            
            if buying_price and quantity:
                try:
                    buying_price = Decimal(buying_price)
                    quantity = Decimal(quantity)
                    
                    # Update or create buying price
                    BuyingPrice.objects.update_or_create(
                        product=product,
                        date=date,
                        defaults={
                            'buying_price_per_kg': buying_price,
                            'quantity_received': quantity,
                            'notes': notes,
                            'recorded_by': request.user,
                        }
                    )
                except (InvalidOperation, ValueError):
                    continue
    
    messages.success(request, 'Buying prices saved successfully.')
    return JsonResponse({'success': True})


def _compute_profit_loss(butchery_id, start_date, end_date):
    qs = DailyStock.objects.select_related('product').prefetch_related('transfers_out')
    if start_date:
        qs = qs.filter(date__gte=start_date)
    if end_date:
        qs = qs.filter(date__lte=end_date)
    if butchery_id:
        qs = qs.filter(product__butchery_id=butchery_id)

    prod = {}
    total_revenue = total_cogs = wastage_cost = Decimal('0.00')
    for ds in qs:
        buying_price = ds.unit_buying_price
        sold = ds.sold
        revenue = ds.revenue
        row = prod.setdefault(ds.product.name, {
            'name': ds.product.name,
            'sold': Decimal('0'),
            'revenue': Decimal('0.00'),
            'cogs': Decimal('0.00'),
            'wastage': Decimal('0'),
        })
        row['sold'] += sold
        row['revenue'] += revenue
        row['cogs'] += sold * buying_price
        row['wastage'] += ds.wastage
        total_revenue += revenue
        total_cogs += sold * buying_price
        wastage_cost += ds.wastage * buying_price

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
    low_stock_products = list(_low_stock_qs().select_related('category', 'butchery'))
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
            expense_date = form.cleaned_data.get('date')
            # Date validation: non-superusers can only create expenses for today
            if not request.user.is_superuser and expense_date and expense_date != localdate():
                messages.error(request, 'You can only record expenses for today. Past dates are read-only. Contact the admin to make changes to past dates.')
                return render(request, 'expense_create.html', {'form': form})
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
