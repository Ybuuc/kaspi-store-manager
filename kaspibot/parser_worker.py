"""One background job per process. Launched by the Flask parser page."""
import asyncio
import json
import sys
from datetime import datetime


def run(job_id):
    from app import create_app
    app = create_app()
    from kaspibot.models import db, ParserJob
    from kaspibot.services.kaspi_parser import collect
    from kaspibot.services.parser_integration import apply_observation

    with app.app_context():
        claimed = ParserJob.query.filter_by(
            id=job_id,
            status='queued',
            active_slot='kaspi'
        ).update({
            'status': 'running',
            'message': 'Запуск Chromium…'
        })

        db.session.commit()

        if not claimed:
            return

        job = db.session.get(ParserJob, job_id)
        config = json.loads(job.config_json)

        def progress(message):
            job.message = message
            db.session.commit()

        try:
            asyncio.run(
                asyncio.wait_for(
                    collect(
                        config,
                        lambda item: apply_observation(job_id, item),
                        progress
                    ),
                    timeout=1800
                )
            )

            job.status = 'partial' if job.errors else 'completed'

        except Exception as exc:
            db.session.rollback()
            job = db.session.get(ParserJob, job_id)
            job.status = 'failed'

            if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                job.message = (
                    'Превышен лимит 30 минут. '
                    'Собранные результаты сохранены.'
                )
            else:
                job.message = f'{type(exc).__name__}: {exc}'

        finally:
            job.active_slot = None
            job.finished_at = datetime.utcnow()
            db.session.commit()


if __name__ == '__main__':
    run(int(sys.argv[1]))