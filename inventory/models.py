from decimal import Decimal
from functools import cached_property

from django.db import models, transaction
from django.contrib.auth.models import User
from django.utils.timezone import now, localdate


def today():
    return localdate()


def buying_price_on(product, date):
    """Slaughterhouse price recorded for `date`, else the latest one before it, else None."""
    record = (
        BuyingPrice.objects.filter(product=product, date__lte=date)
        .order_by('-date').values_list('buying_price_per_kg', flat=True).first()
    )
    return record


class MeatCategory(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = 'Meat Categories'

    def __str__(self):
        return self.name


class Butchery(models.Model):
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=300)
    phone = models.CharField(max_length=15)
    manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='managed_butcheries'
    )
    serves_schools = models.BooleanField(
        default=False, help_text="Shows the School Deliveries section on this branch's daily entry",
    )

    class Meta:
        verbose_name_plural = 'Butcheries'

    def __str__(self):
        return self.name


class MeatProduct(models.Model):
    UNIT_CHOICES = [
        ('KG', 'Kilogram'),
        ('PC', 'Piece'),
    ]

    category = models.ForeignKey(MeatCategory, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=200)
    buying_price = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=2, choices=UNIT_CHOICES, default='KG')
    current_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    minimum_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='products')
    last_updated = models.DateTimeField(auto_now=True)

    def is_low_stock(self):
        return self.current_stock <= self.minimum_stock
    is_low_stock.boolean = True

    def __str__(self):
        return f"{self.name} ({self.butchery.name})"


class StockMovement(models.Model):
    MOVEMENT_CHOICES = [
        ('IN', 'Stock In'),
        ('OUT', 'Stock Out'),
        ('WASTE', 'Waste'),
        ('TRANSFER', 'Transfer'),
    ]

    product = models.ForeignKey(MeatProduct, on_delete=models.CASCADE, related_name='movements')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    movement_type = models.CharField(max_length=10, choices=MOVEMENT_CHOICES)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    timestamp = models.DateTimeField(default=now)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_movements'
    )

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.movement_type} - {self.product.name} ({self.quantity})"


class DailyStock(models.Model):
    """One row per product per branch per day.

    sold = opening + received - transferred out - wastage - closing
    """
    product = models.ForeignKey(MeatProduct, on_delete=models.CASCADE, related_name='daily_stocks')
    date = models.DateField(default=today)
    opening_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    received = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closing_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    wastage = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # Prices at the time of entry, so later price changes don't rewrite history.
    buying_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='daily_stocks'
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ('product', 'date')
        ordering = ['-date']

    @property
    def transferred_out(self):
        return sum((t.quantity for t in self.transfers_out.all()), Decimal('0'))

    @property
    def available(self):
        return self.opening_stock + self.received - self.transferred_out - self.wastage

    @property
    def sold(self):
        qty = self.available - self.closing_stock
        return qty if qty > 0 else Decimal('0')

    @property
    def shortfall(self):
        """Stock counted above what was available; indicates a counting error."""
        qty = self.closing_stock - self.available
        return qty if qty > 0 else Decimal('0')

    @property
    def unit_selling_price(self):
        return self.selling_price if self.selling_price is not None else self.product.selling_price

    @cached_property
    def school_totals(self):
        """(kg, amount) delivered to schools from this product on this day."""
        result = SchoolDelivery.objects.filter(product_id=self.product_id, date=self.date).aggregate(
            qty=models.Sum('quantity'),
            amount=models.Sum(models.F('quantity') * models.F('unit_price'),
                              output_field=models.DecimalField(max_digits=14, decimal_places=2)),
        )
        return result['qty'] or Decimal('0'), result['amount'] or Decimal('0')

    @property
    def unit_buying_price(self):
        recorded = buying_price_on(self.product, self.date)
        if recorded is not None:
            return recorded
        return self.buying_price if self.buying_price is not None else self.product.buying_price

    @property
    def revenue(self):
        """Shop sales at the shop price plus school deliveries at the school price."""
        school_qty, school_amount = self.school_totals
        shop_qty = self.sold - school_qty
        return max(shop_qty, Decimal('0')) * self.unit_selling_price + school_amount

    @property
    def cost_of_sales(self):
        return self.sold * self.unit_buying_price

    @property
    def gross_profit(self):
        return self.revenue - self.cost_of_sales

    @property
    def wastage_cost(self):
        return self.wastage * self.unit_buying_price

    @property
    def closing_value(self):
        return self.closing_stock * self.unit_buying_price

    def __str__(self):
        return f"{self.product.name} - {self.date}"


# ---------------------------------------------------------------------------
# Daily transfers (allows multiple transfers per product per day)
# ---------------------------------------------------------------------------
class DailyTransfer(models.Model):
    """Represents a transfer of stock from one branch to another on a specific day."""
    source_daily_stock = models.ForeignKey(
        DailyStock, on_delete=models.CASCADE, related_name='transfers_out',
        help_text="The daily stock entry from which stock is transferred"
    )
    to_butchery = models.ForeignKey(
        Butchery, on_delete=models.CASCADE, related_name='daily_transfers_in',
        help_text="Branch receiving the transfer"
    )
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Quantity transferred"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.quantity} {self.source_daily_stock.product.unit} to {self.to_butchery.name}"


class DailyBranchSummary(models.Model):
    """Per-branch per-day summary: M-Pesa amount and staff notes to owner."""
    butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='daily_summaries')
    date = models.DateField(default=today)
    mpesa_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cash_counted = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Physical cash handed over at close of day",
    )
    notes = models.TextField(blank=True, help_text="Staff notes to owner")
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='daily_summaries'
    )
    is_closed = models.BooleanField(default=False)
    closed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='closed_days'
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('butchery', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.butchery.name} - {self.date}"


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------
class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = 'Expense Categories'

    def __str__(self):
        return self.name


class Expense(models.Model):
    butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField()
    date = models.DateField(default=today)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses'
    )
    receipt_image = models.ImageField(upload_to='expenses/', blank=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.description[:50]} - {self.amount}"


# ---------------------------------------------------------------------------
# Staff management
# ---------------------------------------------------------------------------
class Staff(models.Model):
    ROLE_CHOICES = [
        ('MANAGER', 'Manager'),
        ('CASHIER', 'Cashier'),
        ('BUTCHER', 'Butcher'),
        ('SUPERVISOR', 'Supervisor'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile')
    butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='staff')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    phone = models.CharField(max_length=15)
    id_number = models.CharField(max_length=20, unique=True)
    date_hired = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'Staff'

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.role})"


class Shift(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name='shifts')
    date = models.DateField(default=today)
    start_time = models.TimeField()
    end_time = models.TimeField(blank=True, null=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.staff} - {self.date}"


# ---------------------------------------------------------------------------
# Date permissions for editing past/future data
# ---------------------------------------------------------------------------
class DatePermission(models.Model):
    """Grants permission to a user to edit data for specific date ranges."""
    PERMISSION_TYPE_CHOICES = [
        ('EDIT', 'Edit existing data'),
        ('BACKFILL', 'Add missing data'),
        ('FULL', 'Full access (edit and backfill)'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='date_permissions')
    butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='date_permissions')
    start_date = models.DateField(help_text="Start date of the permission range", default=today)
    end_date = models.DateField(help_text="End date of the permission range", default=today)
    permission_type = models.CharField(max_length=10, choices=PERMISSION_TYPE_CHOICES, default='FULL')
    granted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='granted_permissions'
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, help_text="Reason for granting permission")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-start_date', '-granted_at']

    def __str__(self):
        if self.start_date == self.end_date:
            return f"{self.user.username} - {self.butchery.name} - {self.start_date} ({self.permission_type})"
        return f"{self.user.username} - {self.butchery.name} - {self.start_date} to {self.end_date} ({self.permission_type})"
    
    def covers_date(self, date):
        """Check if this permission covers a specific date."""
        return self.start_date <= date <= self.end_date


# ---------------------------------------------------------------------------
# Schools supplied on credit (paid weekly by cheque)
# ---------------------------------------------------------------------------
class School(models.Model):
    butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='schools')
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SchoolPrice(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='prices')
    product = models.ForeignKey(MeatProduct, on_delete=models.CASCADE, related_name='school_prices')
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('school', 'product')

    def __str__(self):
        return f"{self.school} - {self.product.name}: {self.price_per_unit}"


class SchoolDelivery(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='deliveries')
    product = models.ForeignKey(MeatProduct, on_delete=models.PROTECT, related_name='school_deliveries')
    date = models.DateField(default=today)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    # School price at the time of delivery.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='school_deliveries'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', 'school__name']
        verbose_name_plural = 'School deliveries'

    @property
    def amount(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.date} {self.school}: {self.quantity} {self.product.name}"


class SchoolPayment(models.Model):
    school = models.ForeignKey(School, on_delete=models.PROTECT, related_name='payments')
    week_start = models.DateField(help_text="Monday of the delivery week this cheque pays for")
    cheque_number = models.CharField(max_length=50)
    bank = models.CharField(max_length=100, blank=True)
    cheque_date = models.DateField(default=today)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='school_payments'
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-week_start', 'school__name']

    def __str__(self):
        return f"{self.school} cheque {self.cheque_number} ({self.amount})"


# ---------------------------------------------------------------------------
# Buying prices for accurate P&L calculations
# ---------------------------------------------------------------------------
class BuyingPrice(models.Model):
    """Tracks buying prices from slaughter house by product and date."""
    product = models.ForeignKey(MeatProduct, on_delete=models.CASCADE, related_name='buying_prices')
    date = models.DateField(help_text="Date when this price was paid")
    buying_price_per_kg = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price per kg from slaughter house")
    quantity_received = models.DecimalField(max_digits=10, decimal_places=2, help_text="Quantity received (in kg)")
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='recorded_buying_prices')
    recorded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, help_text="Optional notes about this purchase")

    class Meta:
        unique_together = ('product', 'date')
        ordering = ['-date', '-recorded_at']

    def __str__(self):
        return f"{self.product.name} - {self.date} - {self.buying_price_per_kg}/kg"


# ---------------------------------------------------------------------------
# Stock transfers
# ---------------------------------------------------------------------------
class StockTransfer(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    from_butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='transfers_out')
    to_butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='transfers_in')
    product = models.ForeignKey(MeatProduct, on_delete=models.CASCADE, related_name='transfers')
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateTimeField(default=now)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    requested_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfers_requested'
    )
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='transfers_approved'
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-date']

    def approve(self, user):
        """Move stock between butcheries and record movements atomically."""
        with transaction.atomic():
            source = self.product
            dest, _ = MeatProduct.objects.get_or_create(
                name=source.name,
                butchery=self.to_butchery,
                defaults={
                    'category': source.category,
                    'buying_price': source.buying_price,
                    'selling_price': source.selling_price,
                    'unit': source.unit,
                    'current_stock': 0,
                    'minimum_stock': source.minimum_stock,
                },
            )
            MeatProduct.objects.filter(pk=source.pk).update(
                current_stock=models.F('current_stock') - self.quantity
            )
            MeatProduct.objects.filter(pk=dest.pk).update(
                current_stock=models.F('current_stock') + self.quantity
            )
            StockMovement.objects.create(
                product=source, quantity=self.quantity, movement_type='TRANSFER',
                unit_price=source.buying_price, recorded_by=user,
                notes=f"Transfer OUT to {self.to_butchery.name}",
            )
            StockMovement.objects.create(
                product=dest, quantity=self.quantity, movement_type='TRANSFER',
                unit_price=source.buying_price, recorded_by=user,
                notes=f"Transfer IN from {self.from_butchery.name}",
            )
            self.status = 'APPROVED'
            self.approved_by = user
            self.save()

    def __str__(self):
        return f"Transfer {self.product.name} ({self.quantity}) {self.from_butchery} -> {self.to_butchery}"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action = models.CharField(max_length=50)
    model_name = models.CharField(max_length=100)
    object_id = models.IntegerField()
    description = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.action} by {self.user} at {self.timestamp}"
