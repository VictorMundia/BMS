from django.apps import AppConfig
from django.db.models.signals import post_migrate


def create_permissions_and_groups(sender, **kwargs):
    """Auto-create custom permissions and user groups after migrations."""
    from django.contrib.auth.models import Group, Permission
    from django.contrib.contenttypes.models import ContentType
    from inventory.models import MeatProduct

    content_type = ContentType.objects.get_for_model(MeatProduct)

    view_reports, _ = Permission.objects.get_or_create(
        codename='view_reports',
        content_type=content_type,
        defaults={'name': 'Can view inventory reports'},
    )
    manage_stock, _ = Permission.objects.get_or_create(
        codename='manage_stock',
        content_type=content_type,
        defaults={'name': 'Can manage stock movements'},
    )

    managers, _ = Group.objects.get_or_create(name='Inventory Managers')
    managers.permissions.add(view_reports, manage_stock)

    staff, _ = Group.objects.get_or_create(name='Inventory Staff')
    staff.permissions.add(view_reports)


class InventoryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'

    def ready(self):
        post_migrate.connect(create_permissions_and_groups, sender=self)
