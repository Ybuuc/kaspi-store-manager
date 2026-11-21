"""Production web entry point; the API import worker never imports this module."""
from app import create_app
from kaspibot.services.automatic_sync import start

app = create_app()
start(app)
