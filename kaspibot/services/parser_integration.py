"""Database mapping and background-run lifecycle for the Kaspi parser."""

import json
from datetime import datetime, timedelta

from kaspibot.models import db, Product, ParserJob, ParserObservation
from kaspibot.services.kaspi_parser import extract_product_id


def expire_stale_jobs():
    # Парсер ограничен 30 минутами.
    # Дополнительные 5 минут — на запуск и завершение процесса.
    jobs = ParserJob.query.filter(
        ParserJob.active_slot == 'kaspi',
        ParserJob.created_at
        < datetime.utcnow() - timedelta(minutes=35),
    ).all()

    for job in jobs:
        job.status = 'failed'
        job.active_slot = None
        job.finished_at = datetime.utcnow()
        job.message = (
            'Процесс прерван или превышено время ожидания. '
            'Можно запустить проверку снова.'
        )

    if jobs:
        db.session.commit()


def apply_observation(job_id, item):
    """Сохраняет результат, не меняя остатки, aliases и закупочные цены."""

    job = db.session.get(ParserJob, job_id)

    if not job or job.status != 'running':
        raise RuntimeError('Проверка уже завершена.')

    try:
        with db.session.begin_nested():
            # 1. Ищем товар по Kaspi ID.
            product = Product.query.filter_by(
                kaspi_product_id=item['kaspi_id']
            ).first()

            # 2. Ищем по ID внутри ссылки Kaspi.
            if product is None:
                candidates = [
                    p
                    for p in Product.query.filter(
                        Product.kaspi_url.isnot(None)
                    ).all()
                    if (
                        extract_product_id(p.kaspi_url)
                        == item['kaspi_id']
                    )
                ]

                if len(candidates) > 1:
                    raise ValueError(
                        'Несколько складских товаров с одной '
                        'ссылкой Kaspi; требуется ручная привязка.'
                    )

                product = candidates[0] if candidates else None

            # 3. Ищем по нормализованному названию.
            if product is None:
                normal = lambda name: ' '.join(
                    (name or '').casefold().split()
                )

                candidates = [
                    p
                    for p in Product.query.all()
                    if normal(p.name) == normal(item['name'])
                ]

                if len(candidates) > 1:
                    raise ValueError(
                        'Неоднозначное совпадение названий; '
                        'требуется ручная привязка.'
                    )

                product = candidates[0] if candidates else None

            # Не перезаписываем существующую привязку к другому ID.
            if (
                product is not None
                and product.kaspi_product_id
                not in (None, '', item['kaspi_id'])
            ):
                raise ValueError(
                    'У товара уже другой Kaspi ID; '
                    'автоматическая привязка отменена.'
                )

            # 4. Создаём новый товар с нулевыми остатками.
            if product is None:
                product = Product(
                    name=item['name'],
                    purchase_price=0,
                    stock_qty=0,
                    reserved_qty=0,
                    onway_qty=0,
                    is_active=True,
                )

                db.session.add(product)

            # Обновляем сведения о карточке.
            product.kaspi_product_id = item['kaspi_id']
            product.kaspi_url = item['url']

            if item.get('image_url'):
                product.image_url = item['image_url']

            # Цена обновляется только при подтверждении своего продавца.
            if (
                item.get('our_price') is not None
                and not item.get('error')
            ):
                product.current_price = item['our_price']

            product.last_synced_at = datetime.utcnow()

            db.session.flush()
            product_id = product.id

    except Exception as exc:
        product_id = None

        item = {
            **item,
            'error': (
                f"{item.get('error') or ''} "
                f'Ошибка привязки: {exc}'
            ).strip(),
        }

    # Историю сохраняем и при ошибке привязки товара.
    db.session.add(
        ParserObservation(
            job_id=job_id,
            product_id=product_id,
            kaspi_id=item['kaspi_id'],
            data_json=json.dumps(
                item,
                ensure_ascii=False,
            ),
        )
    )

    job.processed += 1
    job.errors += int(bool(item.get('error')))

    db.session.commit()


def latest_observations():
    """Возвращает последний результат парсера для каждого товара."""

    latest = (
        db.session.query(
            db.func.max(ParserObservation.id).label('id')
        )
        .filter(
            ParserObservation.product_id.isnot(None)
        )
        .group_by(
            ParserObservation.product_id
        )
        .subquery()
    )

    return {
        row.product_id: row
        for row in ParserObservation.query.join(
            latest,
            ParserObservation.id == latest.c.id,
        ).all()
    }