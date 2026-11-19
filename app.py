"""Archive-first application: one catalogue, one SQLite file."""
from kaspibot.application import create_app
from management import hash_password, verify_password, price_cents, stock_value

__all__ = ['create_app', 'hash_password', 'verify_password', 'price_cents', 'stock_value']

if __name__ == '__main__':
    create_app().run()
