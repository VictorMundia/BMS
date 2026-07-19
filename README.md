# Butchery Management System (BMS)

Full-stack web-based Butchery Management System built with Django and PostgreSQL.
Manages inventory, stock movements, purchases, suppliers, and reporting across
multiple butchery locations.

## Tech Stack
- Python 3.10+, Django 4.2
- PostgreSQL 16
- Django Templates + Flowbite 2.2.1 + Tailwind CSS (CDN)
- Django built-in authentication

## Setup

1. Create and activate a virtual environment:
   ```
   python -m venv venv
   venv\Scripts\activate        # Windows
   ```
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a PostgreSQL database named `bms_db` (user `postgres`, host `localhost`,
   port `5432`). Update the password in `butchery_system/settings.py` if needed.
4. Apply migrations:
   ```
   python manage.py makemigrations
   python manage.py migrate
   ```
5. Create a superuser:
   ```
   python manage.py createsuperuser
   ```
6. Run the development server:
   ```
   python manage.py runserver
   ```
7. Visit http://127.0.0.1:8000/

## Access Control
- The `post_migrate` signal auto-creates custom permissions (`view_reports`,
  `manage_stock`) and groups (`Inventory Managers`, `Inventory Staff`).
- Assign users to a group (or mark them staff) via the Django admin to grant
  access to the `/inventory/` section.

## Key URLs
- `/` — Home dashboard
- `/login/`, `/logout/` — Authentication
- `/inventory/stock-movement/` — Record stock movement
- `/inventory/purchase/create/` — Create purchase order
- `/inventory/reports/` — Reporting dashboard
- `/inventory/reports/export/` — Export stock report CSV
- `/admin/` — Django admin
