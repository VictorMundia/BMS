from django.core.management.base import BaseCommand
from inventory.models import MeatProduct, Butchery


class Command(BaseCommand):
    help = 'Remove specific products from a butchery'

    def add_arguments(self, parser):
        parser.add_argument('butchery_name', type=str, help='Name of the butchery')
        parser.add_argument('product_names', nargs='+', type=str, help='Names of products to remove')

    def handle(self, *args, **options):
        butchery_name = options['butchery_name']
        product_names = options['product_names']

        try:
            butchery = Butchery.objects.get(name__icontains=butchery_name)
        except Butchery.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Butchery "{butchery_name}" not found'))
            return

        deleted_count = 0
        for product_name in product_names:
            products = MeatProduct.objects.filter(butchery=butchery, name__icontains=product_name)
            count = products.count()
            if count > 0:
                products.delete()
                deleted_count += count
                self.stdout.write(self.style.SUCCESS(f'Deleted {count} product(s) matching "{product_name}" from {butchery.name}'))
            else:
                self.stdout.write(self.style.WARNING(f'No products found matching "{product_name}" in {butchery.name}'))

        self.stdout.write(self.style.SUCCESS(f'Total products deleted: {deleted_count}'))
