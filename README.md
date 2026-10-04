# Butchery Management System (BMS)

Django web app for running several butchery branches. Staff record daily stock,
transfers to other branches, expenses, M-Pesa takings and cash handed over; the
owner gets a live dashboard, cash check, buying prices, profit & loss reports and
an audit trail. Deployed on Heroku (`bms-app`).

## Tech Stack
- Python 3.11, Django 4.2
- PostgreSQL in production (`DATABASE_URL`), SQLite locally
- Django Templates + Flowbite + Tailwind CSS (CDN), Jazzmin admin, WhiteNoise for static files

## Local Setup
1. `python -m venv venv` then `venv\Scripts\activate`
2. `pip install -r requirements.txt`
3. Create a `.env` file containing `DEBUG=1`
4. `python manage.py migrate`
5. `python manage.py createsuperuser`
6. `python manage.py runserver` and open http://127.0.0.1:8000/

Run tests with `python manage.py test`.

## Production Config Vars
| Variable | Example |
|---|---|
| `DEBUG` | `0` |
| `SECRET_KEY` | long random string (required) |
| `ALLOWED_HOSTS` | `myapp.herokuapp.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://myapp.herokuapp.com` |
| `DATABASE_URL` | set automatically by Heroku Postgres |
| `BUSINESS_DAY_CUTOFF_HOUR` | `4` (Nairobi hour when a new business day starts; default 4) |
| `DJANGO_SUPERUSER_USERNAME` / `_PASSWORD` / `_EMAIL` | optional, creates the first owner on release |

The `Procfile` runs migrations on every release.

## Roles
- **Owner (superuser):** full access.
- **Staff (any other user):** only the daily entry page. Link each staff user to a
  branch (Staff page or admin) so they are locked to that branch.
- Staff can edit only the current business day, unless the owner grants a Date
  Permission (admin) for a past range. The owner can also close a day from the
  branch's daily view, which locks it for staff.
- Sold = opening + received − transfers out − wastage − remaining. The receiving
  branch records transferred stock under "Received".

## Maintenance
- `python manage.py find_duplicate_expenses` lists expenses that look duplicated;
  add `--delete` to remove the extra copies.
