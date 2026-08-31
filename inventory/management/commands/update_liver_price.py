from django.core.management.base import BaseCommand
from inventory.models import MeatProduct
from decimal import Decimal


class Command(BaseCommand):
    help = 'Update liver price from 900 to 800'

    def handle(self, *args, **options):
        liver_products = MeatProduct.objects.filter(name='Liver')
        count = 0
        for product in liver_products:
            product.selling_price = Decimal('800.00')
            product.save()
            count += 1
            self.stdout.write(f'Updated {product.name} for {product.butchery.name} to 800/KG')
        self.stdout.write(self.style.SUCCESS(f'Successfully updated {count} liver products'))
