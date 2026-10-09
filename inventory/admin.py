from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import (
    MeatCategory, Butchery, MeatProduct, StockMovement,
    DailyStock, DailyBranchSummary, ExpenseCategory, Expense,
    Staff, Shift, StockTransfer, AuditLog, DatePermission, BuyingPrice,
    School, SchoolPrice, SchoolDelivery, SchoolPayment,
)


class SchoolPriceInline(admin.TabularInline):
    model = SchoolPrice
    extra = 2

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'product':
            kwargs['queryset'] = MeatProduct.objects.filter(butchery__serves_schools=True).select_related('butchery')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ('name', 'butchery', 'contact_person', 'phone', 'is_active')
    list_filter = ('butchery', 'is_active')
    search_fields = ('name', 'contact_person')
    inlines = [SchoolPriceInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'butchery':
            kwargs['queryset'] = Butchery.objects.filter(serves_schools=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(SchoolDelivery)
class SchoolDeliveryAdmin(admin.ModelAdmin):
    list_display = ('date', 'school', 'product', 'quantity', 'unit_price', 'recorded_by')
    list_filter = ('school', 'date')


@admin.register(SchoolPayment)
class SchoolPaymentAdmin(admin.ModelAdmin):
    list_display = ('week_start', 'school', 'cheque_number', 'bank', 'cheque_date', 'amount')
    list_filter = ('school',)
    search_fields = ('cheque_number',)


@admin.register(MeatCategory)
class MeatCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(Butchery)
class ButcheryAdmin(admin.ModelAdmin):
    list_display = ('name', 'location', 'manager', 'serves_schools')
    search_fields = ('name', 'location')


class RestrictedMeatProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'butchery', 'current_stock', 'is_low_stock')
    list_filter = ('category', 'butchery')
    readonly_fields = ('last_updated',)
    search_fields = ('name',)

    def has_add_permission(self, request):
        return request.user.has_perm('inventory.manage_stock') or request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('inventory.manage_stock') or request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('inventory.manage_stock') or request.user.is_superuser


admin.site.register(MeatProduct, RestrictedMeatProductAdmin)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'movement_type', 'quantity', 'timestamp')
    list_filter = ('movement_type', 'timestamp')


@admin.register(DailyStock)
class DailyStockAdmin(admin.ModelAdmin):
    list_display = ('product', 'date', 'opening_stock', 'received', 'closing_stock', 'wastage')
    list_filter = ('date', 'product__butchery')


@admin.register(DailyBranchSummary)
class DailyBranchSummaryAdmin(admin.ModelAdmin):
    list_display = ('butchery', 'date', 'mpesa_amount')
    list_filter = ('date', 'butchery')


class CustomUserAdmin(UserAdmin):
    """Non-superusers can only view/edit their own account."""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(pk=request.user.pk)

    def has_change_permission(self, request, obj=None):
        if obj is not None and not request.user.is_superuser:
            return obj.pk == request.user.pk
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)


admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# ---------------------------------------------------------------------------
# Other model admin registrations
# ---------------------------------------------------------------------------
@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('butchery', 'category', 'amount', 'date')
    list_filter = ('category', 'date')


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ('user', 'butchery', 'role', 'is_active')
    list_filter = ('butchery', 'role')


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ('staff', 'date', 'start_time', 'end_time')
    list_filter = ('date',)


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ('from_butchery', 'to_butchery', 'product', 'quantity', 'status')
    list_filter = ('status',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'model_name', 'timestamp')
    list_filter = ('action', 'timestamp')
    readonly_fields = ('user', 'action', 'model_name', 'object_id', 'description', 'timestamp', 'ip_address')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DatePermission)
class DatePermissionAdmin(admin.ModelAdmin):
    """Admin interface for managing date permissions."""
    list_display = ('get_user_display', 'butchery', 'get_date_range_display', 'get_permission_type_display', 'is_active', 'granted_by', 'granted_at')
    list_filter = ('permission_type', 'is_active', 'butchery')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name', 'butchery__name', 'notes')
    readonly_fields = ('granted_at', 'granted_by')
    list_per_page = 25
    
    fieldsets = (
        ('Permission Details', {
            'fields': ('user', 'butchery', 'start_date', 'end_date', 'permission_type'),
            'description': 'Select the user, branch, and date range for this permission.'
        }),
        ('Status', {
            'fields': ('is_active',),
            'description': 'Toggle whether this permission is currently active.'
        }),
        ('Additional Information', {
            'fields': ('notes',),
            'description': 'Optional notes explaining why this permission was granted.'
        }),
        ('Audit Information', {
            'fields': ('granted_by', 'granted_at'),
            'classes': ('collapse',),
            'description': 'Automatically tracked information about who granted this permission and when.'
        }),
    )
    
    def get_user_display(self, obj):
        """Display user with their full name if available."""
        if obj.user.first_name and obj.user.last_name:
            return f"{obj.user.get_full_name()} ({obj.user.username})"
        return obj.user.username
    get_user_display.short_description = 'User'
    get_user_display.admin_order_field = 'user__username'
    
    def get_date_range_display(self, obj):
        """Display date range in a readable format."""
        if obj.start_date == obj.end_date:
            return obj.start_date.strftime('%Y-%m-%d')
        return f"{obj.start_date.strftime('%Y-%m-%d')} to {obj.end_date.strftime('%Y-%m-%d')}"
    get_date_range_display.short_description = 'Date Range'
    get_date_range_display.admin_order_field = 'start_date'
    
    def get_permission_type_display(self, obj):
        """Display permission type with color-coded badge."""
        colors = {
            'EDIT': 'blue',
            'BACKFILL': 'green', 
            'FULL': 'purple',
        }
        color = colors.get(obj.permission_type, 'gray')
        return f'<span style="background-color: {color}; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold;">{obj.get_permission_type_display()}</span>'
    get_permission_type_display.short_description = 'Permission Type'
    get_permission_type_display.allow_tags = True
    
    def save_model(self, request, obj, form, change):
        if not change:  # Only set granted_by when creating new permission
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(BuyingPrice)
class BuyingPriceAdmin(admin.ModelAdmin):
    """Admin interface for managing buying prices."""
    list_display = ('product', 'date', 'buying_price_per_kg', 'quantity_received', 'recorded_by', 'recorded_at')
    list_filter = ('date', 'product__category', 'product')
    search_fields = ('product__name', 'notes')
    readonly_fields = ('recorded_at',)
    list_per_page = 25
    date_hierarchy = 'date'
    
    fieldsets = (
        ('Purchase Details', {
            'fields': ('product', 'date', 'buying_price_per_kg', 'quantity_received'),
            'description': 'Enter the buying price and quantity received from the slaughter house.'
        }),
        ('Additional Information', {
            'fields': ('notes',),
            'description': 'Optional notes about this purchase.'
        }),
        ('Audit Information', {
            'fields': ('recorded_by', 'recorded_at'),
            'classes': ('collapse',),
            'description': 'Automatically tracked information.'
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Only set recorded_by when creating new buying price
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)
