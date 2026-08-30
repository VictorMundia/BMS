import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'butchery_system.settings')
django.setup()

from inventory.models import Butchery, MeatCategory, MeatProduct, Staff
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import date

User = get_user_model()

# Create branches
branches_data = [
    {'name': 'Starlight', 'location': 'Nairobi CBD', 'phone': '+254700000001'},
    {'name': 'Slopes', 'location': 'Westlands', 'phone': '+254700000002'},
    {'name': 'Supa', 'location': 'Karen', 'phone': '+254700000003'},
    {'name': 'Smiles', 'location': 'Eastleigh', 'phone': '+254700000004'},
]

for branch_data in branches_data:
    branch, created = Butchery.objects.get_or_create(
        name=branch_data['name'],
        defaults={
            'location': branch_data['location'],
            'phone': branch_data['phone'],
        }
    )
    if created:
        print(f'Created branch: {branch.name}')

# Create categories
categories_data = [
    {'name': 'Beef', 'description': 'Beef meat products'},
    {'name': 'Goat Meat', 'description': 'Goat meat products'},
    {'name': 'Chicken', 'description': 'Chicken products'},
    {'name': 'Offal', 'description': 'Offal products'},
]

for cat_data in categories_data:
    category, created = MeatCategory.objects.get_or_create(
        name=cat_data['name'],
        defaults={'description': cat_data['description']}
    )
    if created:
        print(f'Created category: {category.name}')

# Create products with specified prices
products_data = [
    {'name': 'Beef', 'category': 'Beef', 'selling_price': 860, 'unit': 'KG'},
    {'name': 'Goat', 'category': 'Goat Meat', 'selling_price': 1000, 'unit': 'KG'},
    {'name': 'Liver', 'category': 'Offal', 'selling_price': 900, 'unit': 'KG'},
    {'name': 'Chicken', 'category': 'Chicken', 'selling_price': 620, 'unit': 'PC'},
    {'name': 'Kienyeji Chicken', 'category': 'Chicken', 'selling_price': 1000, 'unit': 'PC'},
    {'name': 'Minced Meat', 'category': 'Beef', 'selling_price': 960, 'unit': 'KG'},
    {'name': 'Matumbo', 'category': 'Offal', 'selling_price': 440, 'unit': 'KG'},
]

branches = Butchery.objects.all()
for product_data in products_data:
    category = MeatCategory.objects.get(name=product_data['category'])
    for branch in branches:
        product, created = MeatProduct.objects.get_or_create(
            name=product_data['name'],
            butchery=branch,
            category=category,
            defaults={
                'buying_price': Decimal('0.00'),
                'selling_price': Decimal(str(product_data['selling_price'])),
                'current_stock': Decimal('0.00'),
                'unit': product_data['unit'],
            }
        )
        if created:
            print(f'Created product: {product.name} for {branch.name} at {product_data["selling_price"]}/{product_data["unit"]}')

# Create butcher accounts and staff
butcher_accounts = [
    {'username': 'starlight', 'password': 'starlight123', 'branch': 'Starlight'},
    {'username': 'slopes', 'password': 'slopes123', 'branch': 'Slopes'},
    {'username': 'supa', 'password': 'supa123', 'branch': 'Supa'},
    {'username': 'smiles', 'password': 'smiles123', 'branch': 'Smiles'},
]

for account in butcher_accounts:
    user, created = User.objects.get_or_create(
        username=account['username'],
        defaults={
            'email': f'{account["username"]}@bms.com',
            'is_superuser': False,
            'is_staff': False,
        }
    )
    if created:
        user.set_password(account['password'])
        user.save()
        print(f'Created user: {account["username"]}')
    else:
        user.set_password(account['password'])
        user.is_superuser = False
        user.is_staff = False
        user.save()
        print(f'Updated user: {account["username"]}')
    
    # Create staff record
    branch = Butchery.objects.get(name=account['branch'])
    staff, created = Staff.objects.get_or_create(
        user=user,
        defaults={
            'butchery': branch,
            'role': 'BUTCHER',
            'phone': '0000000000',
            'id_number': f'1000{branches_data.index(next(b for b in branches_data if b["name"] == account["branch"])) + 1}',
            'date_hired': date.today(),
        }
    )
    if created:
        print(f'Created staff: {account["username"]} -> {branch.name}')
    else:
        staff.butchery = branch
        staff.role = 'BUTCHER'
        staff.save()
        print(f'Updated staff: {account["username"]} -> {branch.name}')

print('Setup complete!')
