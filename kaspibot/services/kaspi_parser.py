"""Read-only Kaspi parser, adapted from PythonProject28/main.py.

The CSS selectors and pricing rules originate in that project. No merchant API
writes are made. Offers are those visible in the selected city, not guaranteed
an exhaustive list of sellers.
"""

import re
from urllib.parse import (
    urljoin,
    urlsplit,
    urlunsplit,
    parse_qsl,
    urlencode,
)


DEFAULT_RULES = dict(
    min_price=2500,
    max_price=15000,
    step=2,
    allow_increase=True,
    fixed_price=False,
)


def validate_rules(value):
    if not isinstance(value, dict):
        raise ValueError(
            'Настройки товара должны быть объектом JSON.'
        )

    rules = {**DEFAULT_RULES, **value}

    if set(rules) != set(DEFAULT_RULES):
        raise ValueError(
            'Неизвестные поля в настройках цены.'
        )

    for key in ('min_price', 'max_price', 'step'):
        if (
            type(rules[key]) is not int
            or not 0 <= rules[key] <= 1_000_000_000
        ):
            raise ValueError(
                f'{key}: требуется целое неотрицательное число.'
            )

    if (
        rules['min_price'] < 1
        or rules['min_price'] > rules['max_price']
    ):
        raise ValueError(
            'Минимальная цена должна быть положительной '
            'и не выше максимальной.'
        )

    for key in ('allow_increase', 'fixed_price'):
        if type(rules[key]) is not bool:
            raise ValueError(
                f'{key}: требуется true или false.'
            )

    return rules


def validate_settings(value):
    if not isinstance(value, dict):
        raise ValueError(
            'Настройки должны быть объектом: Kaspi ID → параметры.'
        )

    result = {}

    for key, rules in value.items():
        if not isinstance(key, str) or not key.isdigit():
            raise ValueError(
                'Ключом настроек должен быть числовой Kaspi ID.'
            )

        result[key] = validate_rules(rules)

    return result


def product_url(href, city=''):
    parts = urlsplit(
        urljoin('https://kaspi.kz', href)
    )

    if (
        parts.scheme != 'https'
        or parts.netloc != 'kaspi.kz'
        or not parts.path.startswith('/shop/p/')
    ):
        raise ValueError(
            'Ожидалась ссылка на карточку kaspi.kz/shop/p/.'
        )

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query)
        if key not in ('m', 'ms', 'c')
    ]

    if city:
        query.append(('c', city))

    return urlunsplit(
        (
            'https',
            'kaspi.kz',
            parts.path,
            urlencode(query),
            '',
        )
    )


async def collect(config, on_product, on_progress):
    # Импорт при запуске: веб-интерфейс доступен и без Chromium.
    from playwright.async_api import async_playwright

    seen = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=config['headless']
        )

        try:
            context = await browser.new_context(
                viewport={
                    'width': 1440,
                    'height': 1000,
                },
                locale='ru-RU',
            )

            context.set_default_timeout(12_000)

            page = await context.new_page()
            detail = await context.new_page()

            async def route_request(route):
                from ipaddress import ip_address

                url = urlsplit(route.request.url)
                host = (url.hostname or '').lower()

                blocked = (
                    url.scheme not in ('http', 'https')
                    or host in ('localhost',)
                    or host.endswith('.local')
                )

                try:
                    blocked = (
                        blocked
                        or not ip_address(host).is_global
                    )
                except ValueError:
                    pass

                if blocked:
                    await route.abort()
                else:
                    await route.continue_()

            await context.route('**/*', route_request)

            for number in range(1, config['max_pages'] + 1):
                on_progress(
                    f'Страница каталога {number}; '
                    f'найдено товаров: {len(seen)}'
                )

                query = {'page': number}

                if config['city']:
                    query['c'] = config['city']

                url = (
                    f"https://kaspi.kz/shop/m/"
                    f"{config['merchant_id']}/products?"
                    f"{urlencode(query)}"
                )

                await page.goto(
                    url,
                    wait_until='domcontentloaded',
                    timeout=60_000,
                )

                try:
                    await page.locator(
                        '.item-card'
                    ).first.wait_for(timeout=20_000)

                except Exception as exc:
                    raise RuntimeError(
                        f'Не найдены карточки на странице {number}. '
                        'Возможны выбор города, ограничение доступа '
                        'или изменение сайта.'
                    ) from exc

                for _ in range(8):
                    await page.mouse.wheel(0, 1500)
                    await page.wait_for_timeout(700)

                cards = page.locator('.item-card')
                batch = []

                for index in range(await cards.count()):
                    card = cards.nth(index)

                    link = card.locator(
                        '.item-card__name-link'
                    ).first

                    if not await link.count():
                        continue

                    href = await link.get_attribute('href')

                    if not href:
                        continue

                    href = product_url(
                        href,
                        config['city'],
                    )

                    kaspi_id = extract_product_id(href)

                    if not kaspi_id or kaspi_id in seen:
                        continue

                    seen.add(kaspi_id)

                    price_node = card.locator(
                        '.item-card__prices-price'
                    ).first

                    image_node = card.locator('img').first

                    image = (
                        await image_node.get_attribute('src')
                        if await image_node.count()
                        else None
                    )

                    if image and not image.startswith('https://'):
                        image = None

                    batch.append(
                        dict(
                            kaspi_id=kaspi_id,
                            name=(await link.inner_text()).strip(),
                            url=href,
                            image_url=image,
                            catalog_price=(
                                parse_price(
                                    await price_node.inner_text()
                                )
                                if await price_node.count()
                                else None
                            ),
                        )
                    )

                if not batch:
                    raise RuntimeError(
                        'Каталог повторяет уже просмотренные товары; '
                        'обход остановлен. Результат может быть неполным.'
                    )

                for item in batch:
                    on_progress(
                        f"Проверка {item['name']}"
                    )

                    try:
                        sellers = await parse_competitors(
                            detail,
                            item['url'],
                        )

                        if not sellers:
                            raise RuntimeError(
                                'Не найдена таблица продавцов: '
                                'проверьте город и доступность карточки.'
                            )

                        own = [
                            row['price']
                            for row in sellers
                            if (
                                row['seller_name'].strip().casefold()
                                == config['seller'].strip().casefold()
                            )
                        ]

                        item['our_price'] = (
                            min(own) if own else None
                        )

                        item['competitors'] = sellers

                        rules = config['settings'].get(
                            item['kaspi_id'],
                            DEFAULT_RULES,
                        )

                        item['pricing'] = calculate_new_price(
                            item['our_price'],
                            sellers,
                            rules['step'],
                            rules['min_price'],
                            rules['max_price'],
                            rules['allow_increase'],
                            rules['fixed_price'],
                            config['seller'],
                        )

                        if item['pricing'] is None:
                            item['pricing'] = {
                                'action': 'keep',
                                'reason': 'no_competitors',
                                'new_price': item['our_price'],
                            }

                        item['error'] = None

                    except Exception as exc:
                        item.update(
                            error=f'{type(exc).__name__}: {exc}',
                            our_price=None,
                            competitors=[],
                            pricing=None,
                        )

                    on_product(item)

                next_link = page.locator(
                    '.pagination__el'
                ).filter(
                    has_text=re.compile(
                        r'^\s*' + str(number + 1) + r'\s*$'
                    )
                )

                if not await next_link.count():
                    on_progress(
                        'Обход завершён; следующая страница '
                        'не обнаружена. Проверены только '
                        'доступные карточки и продавцы.'
                    )
                    break

            else:
                on_progress(
                    f"Достигнут лимит {config['max_pages']} страниц; "
                    'результат может быть неполным.'
                )

        finally:
            await browser.close()


def extract_product_id(url: str) -> str | None:
    match = re.search(
        r'/shop/p/.*?-(\d+)[a-z]?/?(?:\?|$)',
        url,
    )

    if match:
        return match.group(1)

    return None


def parse_price(price_text: str | None) -> int | None:
    if not price_text:
        return None

    digits = re.sub(r'\D', '', price_text)

    if not digits:
        return None

    return int(digits)


async def parse_competitors(page, product_url: str):
    print()
    print('=' * 80)
    print('Открываем карточку товара:')
    print(product_url)
    print('=' * 80)

    await page.goto(
        product_url,
        wait_until='domcontentloaded',
        timeout=60_000,
    )

    await page.locator(
        '.sellers-table__self tbody tr'
    ).first.wait_for(timeout=20_000)

    rows = page.locator(
        '.sellers-table__self tbody tr'
    )

    row_count = await rows.count()

    print(f'Найдено продавцов: {row_count}')

    competitors = []

    for i in range(row_count):
        row = rows.nth(i)

        seller_link = row.locator(
            '.sellers-table__cell a'
        ).first

        if await seller_link.count() == 0:
            continue

        seller_name = (
            await seller_link.inner_text()
        ).strip()

        price_locator = row.locator(
            '.sellers-table__price-cell-text'
            ':not(._installments-price)'
        )

        if await price_locator.count() == 0:
            continue

        price_text = (
            await price_locator.first.inner_text()
        ).strip()

        price = parse_price(price_text)

        if price is None:
            continue

        competitors.append(
            {
                'seller_name': seller_name,
                'price': price,
            }
        )

    print()
    print('Продавцы:')

    for competitor in competitors:
        print(
            f"{competitor['seller_name']}: "
            f"{competitor['price']} ₸"
        )

    return competitors


def calculate_new_price(
    our_price: int | None,
    competitors: list[dict],
    step: int,
    min_price: int,
    max_price: int,
    allow_increase: bool,
    fixed_price: bool,
    our_seller: str = 'PrimeVibe',
):
    if our_price is None:
        return {
            'competitor_name': None,
            'competitor_price': None,
            'our_price': None,
            'calculated_price': None,
            'min_price': min_price,
            'max_price': max_price,
            'new_price': None,
            'action': 'keep',
            'reason': 'our_price_missing',
        }

    if fixed_price:
        return {
            'competitor_name': None,
            'competitor_price': None,
            'our_price': our_price,
            'calculated_price': our_price,
            'min_price': min_price,
            'max_price': max_price,
            'new_price': our_price,
            'action': 'keep',
            'reason': 'fixed_price',
        }

    other_sellers = [
        seller
        for seller in competitors
        if (
            seller['seller_name'].strip().lower()
            != our_seller.strip().lower()
        )
    ]

    if not other_sellers:
        return None

    cheapest = min(
        other_sellers,
        key=lambda seller: seller['price'],
    )

    competitor_price = cheapest['price']

    if competitor_price < our_price:
        calculated_price = competitor_price - step
        action = 'decrease'

    elif competitor_price > our_price:
        if not allow_increase:
            return {
                'competitor_name': cheapest['seller_name'],
                'competitor_price': competitor_price,
                'our_price': our_price,
                'calculated_price': our_price,
                'min_price': min_price,
                'max_price': max_price,
                'new_price': our_price,
                'action': 'keep',
                'reason': 'price_increase_disabled',
            }

        calculated_price = competitor_price - step
        action = 'increase'

    else:
        calculated_price = our_price
        action = 'keep'

    new_price = max(calculated_price, min_price)
    new_price = min(new_price, max_price)

    if not allow_increase:
        new_price = min(new_price, our_price)

    action = (
        'increase'
        if new_price > our_price
        else 'decrease'
        if new_price < our_price
        else 'keep'
    )

    return {
        'competitor_name': cheapest['seller_name'],
        'competitor_price': competitor_price,
        'our_price': our_price,
        'calculated_price': calculated_price,
        'min_price': min_price,
        'max_price': max_price,
        'new_price': new_price,
        'action': action,
        'reason': None,
    }