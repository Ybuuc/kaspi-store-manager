import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import IntegrityError

from kaspibot.models import db, ParserJob, ParserObservation
from kaspibot.services.kaspi_parser import validate_settings
from kaspibot.services.parser_integration import expire_stale_jobs


parser_bp = Blueprint(
    'parser',
    __name__,
    url_prefix='/products/parser',
)


@parser_bp.route('/', methods=['GET', 'POST'])
def page():
    expire_stale_jobs()

    session.setdefault(
        'parser_csrf',
        secrets.token_urlsafe(32),
    )

    previous = ParserJob.query.order_by(
        ParserJob.id.desc()
    ).first()

    root = Path(__file__).resolve().parents[1]

    defaults = dict(
        merchant_id='30319889',
        seller='PrimeVibe',
        city='',
        max_pages=10,
        headless=True,
        settings=json.loads(
            (root / 'parser/product_settings.json').read_text(
                encoding='utf-8'
            )
        ),
    )

    if previous:
        defaults.update(
            json.loads(previous.config_json)
        )

    if request.method == 'POST':
        # Проверяем, что запрос отправлен из формы этой сессии.
        if not secrets.compare_digest(
            request.form.get('csrf', ''),
            session['parser_csrf'],
        ):
            return 'Форма устарела. Обновите страницу.', 400

        try:
            merchant = request.form.get(
                'merchant_id', ''
            ).strip()

            seller = request.form.get(
                'seller', ''
            ).strip()

            city = request.form.get(
                'city', ''
            ).strip()

            pages = int(
                request.form.get('max_pages', '10')
            )

            if (
                not merchant.isascii()
                or not merchant.isdigit()
                or len(merchant) > 20
            ):
                raise ValueError(
                    'Введите числовой ID магазина Kaspi.'
                )

            if not seller or len(seller) > 200:
                raise ValueError(
                    'Введите точное имя вашего продавца.'
                )

            if city and (
                not city.isascii()
                or not city.isdigit()
                or len(city) > 20
            ):
                raise ValueError(
                    'Код города должен состоять из цифр.'
                )

            if not 1 <= pages <= 100:
                raise ValueError(
                    'Число страниц: от 1 до 100.'
                )

            settings_text = request.form.get(
                'settings', '{}'
            )

            if len(settings_text) > 200_000:
                raise ValueError(
                    'Слишком большой JSON настроек.'
                )

            config = dict(
                merchant_id=merchant,
                seller=seller,
                city=city,
                max_pages=pages,
                headless=(
                    request.form.get('headless') == 'on'
                ),
                settings=validate_settings(
                    json.loads(settings_text)
                ),
            )

        except (ValueError, TypeError) as exc:
            flash(
                f'Ошибка настроек: {exc}',
                'error',
            )

            return redirect(
                url_for('kaspibot.parser.page')
            )

        # Создаём задание. Уникальный active_slot блокирует
        # одновременный запуск нескольких проверок.
        job = ParserJob(
            active_slot='kaspi',
            status='queued',
            config_json=json.dumps(
                config,
                ensure_ascii=False,
            ),
        )

        db.session.add(job)

        try:
            db.session.commit()

        except IntegrityError:
            db.session.rollback()

            flash(
                'Проверка уже запущена. Дождитесь завершения.',
                'error',
            )

            return redirect(
                url_for('kaspibot.parser.page')
            )

        try:
            env = dict(os.environ)
            env['KSM_SECRET_KEY'] = current_app.secret_key

            env['KASPIBOT_DATABASE_URL'] = current_app.config[
                'SQLALCHEMY_DATABASE_URI'
            ]

            # Запускаем парсер тем же Python, что и веб-приложение.
            child = subprocess.Popen(
                [
                    sys.executable,
                    '-m',
                    'kaspibot.parser_worker',
                    str(job.id),
                ],
                cwd=current_app.root_path,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Ожидаем завершения процесса в отдельном потоке,
            # чтобы не задерживать ответ веб-страницы.
            import threading

            flask_app = current_app._get_current_object()
            job_id = job.id

            def reap():
                code = child.wait()

                with flask_app.app_context():
                    row = db.session.get(
                        ParserJob,
                        job_id,
                    )

                    if row and row.active_slot:
                        from datetime import datetime

                        row.status = 'failed'
                        row.active_slot = None
                        row.finished_at = datetime.utcnow()

                        row.message = (
                            'Процесс завершился до сохранения '
                            f'результата (код {code}). '
                            'Проверьте установку зависимостей.'
                        )

                        db.session.commit()

            threading.Thread(
                target=reap,
                daemon=True,
            ).start()

        except Exception as exc:
            from datetime import datetime

            job.status = 'failed'
            job.active_slot = None
            job.finished_at = datetime.utcnow()

            job.message = (
                f'Не удалось запустить процесс: {exc}'
            )

            db.session.commit()

        return redirect(
            url_for('kaspibot.parser.page',
                job_id=job.id,
            )
        )

    # GET: показываем выбранную или последнюю проверку.
    selected_id = request.args.get(
        'job_id',
        type=int,
    )

    job = (
        db.get_or_404(ParserJob, selected_id)
        if selected_id
        else previous
    )

    rows = (
        ParserObservation.query.filter_by(
            job_id=job.id
        )
        .order_by(
            ParserObservation.id.desc()
        )
        .all()
        if job
        else []
    )

    return render_template('kaspibot/parser.html',
        config=defaults,
        settings_text=json.dumps(
            defaults['settings'],
            ensure_ascii=False,
            indent=2,
        ),
        job=job,
        observations=rows,
        jobs=(
            ParserJob.query.order_by(
                ParserJob.id.desc()
            )
            .limit(20)
            .all()
        ),
        active=ParserJob.query.filter_by(
            active_slot='kaspi'
        ).first(),
    )