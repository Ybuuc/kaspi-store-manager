"""Run seller import outside the web request; credentials are process-scoped only."""
import sys
from app import create_app
from kaspibot.services.seller_sync import synchronize


def run(run_id,days):
    app=create_app()
    with app.app_context():
        synchronize(days,run_id=run_id)


if __name__=='__main__':
    run(int(sys.argv[1]),int(sys.argv[2]))
