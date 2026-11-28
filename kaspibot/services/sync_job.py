import os
import subprocess
import sys
import threading

from flask import current_app
from kaspibot.models import db, KaspiSyncRun
from kaspibot.services.seller_sync import start_run,utcnow
from kaspibot.services import sync_schedule as schedule


def launch(days):
    if not isinstance(days,int) or not 1 <= days <= 366:
        raise ValueError('История должна быть от 1 до 366 дней')
    run_id=start_run()
    app=current_app._get_current_object()
    env=dict(os.environ,KSM_SECRET_KEY=app.secret_key,KASPI_API_TOKEN=app.config.get('KASPI_API_TOKEN',''),
             KASPIBOT_DATABASE=app.config['DATABASE'],KASPI_API_BASE=app.config['KASPI_API_BASE'])
    for key in ['KASPI_API_SYNC_INTERVAL_SECONDS','KASPI_XML_SYNC_INTERVAL_SECONDS','KASPI_XML_MAX_BACKOFF_SECONDS']:
        env[key]=str(app.config[key])
    try:
        process=subprocess.Popen([sys.executable,'-m','kaspibot.sync_worker',str(run_id),str(days)],
            cwd=app.root_path,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except OSError:
        row=db.session.get(KaspiSyncRun,run_id)
        row.active_slot=None
        row.finished_at=utcnow()
        row.status='failed'
        row.message='Не удалось запустить процесс синхронизации.'
        schedule.finish('api','failed',errors=1,message=row.message,session=db.session)
        db.session.commit()
        raise ValueError(row.message) from None

    def reap():
        code=process.wait()
        with app.app_context():
            row=db.session.get(KaspiSyncRun,run_id)
            if row and row.status in ('queued','running'):
                row.status='failed'
                row.message=f'Процесс синхронизации завершился до результата (код {code}).'
                row.active_slot=None
                row.finished_at=utcnow()
                schedule.finish('api','failed',errors=1,message=row.message,session=db.session)
                db.session.commit()
    threading.Thread(target=reap,daemon=True).start()
    return run_id
