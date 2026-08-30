from decimal import Decimal

from django.db import models, transaction
from django.contrib.auth.models import User
from django.utils.timezone import now, localdate


def today():
    return localdate()


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

    sold = opening + received - wastage - closing
    Revenue/profit are derived from the product's prices.
    """
    product = models.ForeignKey(MeatProduct, on_delete=models.CASCADE, related_name='daily_stocks')
    date = models.DateField(default=today)
    opening_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    received = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closing_stock = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    wastage = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='daily_stocks'
    )
    notes = models.TextField(blank=True)
    # Transfer fields
    transfer_to = models.ForeignKey(
        Butchery, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='daily_transfers_out',
        help_text="Branch to which stock was transferred"
    )
    transfer_from = models.ForeignKey(
        Butchery, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='daily_transfers_in',
        help_text="Branch from which stock was received via transfer"
    )
    transfer_quantity = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Quantity transferred"
    )

    class Meta:
        unique_together = ('product', 'date')
        ordering = ['-date']

    @property
    def sold(self):
        qty = self.opening_stock + self.received - self.wastage - self.closing_stock
        return qty if qty > 0 else Decimal('0')

    @property
    def revenue(self):
        return self.sold * self.product.selling_price

    @property
    def cost_of_sales(self):
        return self.sold * self.product.buying_price

    @property
    def gross_profit(self):
        return self.revenue - self.cost_of_sales

    @property
    def closing_value(self):
        return self.closing_stock * self.product.buying_price

    def __str__(self):
        return f"{self.product.name} - {self.date}"


class DailyBranchSummary(models.Model):
    """Per-branch per-day summary: M-Pesa amount and staff notes to owner."""
    butchery = models.ForeignKey(Butchery, on_delete=models.CASCADE, related_name='daily_summaries')
    date = models.DateField(default=today)
    mpesa_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True, help_text="Staff notes to owner")
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='daily_summaries'
    )

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
