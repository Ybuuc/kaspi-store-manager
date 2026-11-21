"""Local non-AI baseline: read-only navigation help, no external requests."""
FAQ = [
    ('backup', ('backup', 'бэкап', 'резерв', 'копия базы', 'сақтық'),
     'Резервная копия: остановите изменения и выполните backup-db или tools/backup_archive.py. Копия включает всю app.db.'),
    ('price', ('цен', 'баға', 'price'),
     'Цена: откройте /management/products, карточку товара и «Бағаны өзгерту». История цены доступна администратору.'),
    ('stock', ('остат', 'қалдық', 'stock'),
     'Остаток: откройте карточку товара и изменение остатка. Укажите целое число от 0. Доступный остаток учитывает резерв и товары в пути.'),
    ('archive', ('архив', 'archive'),
     'Архив товаров: /management/products/archive. Восстановление сохраняет прежний ID товара и его движения.'),
    ('roles', ('роль', 'рол', 'администратор', 'employee', 'admin'),
     'Роли меняет администратор в /management/admin. Сотруднику закрыты административные отчёты и управление ролями.'),
    ('import', ('excel', 'эксель', 'импорт', 'xlsx'),
     'Excel-импорт: /import. Перед применением проверьте соответствие названий и aliases; повторный файл распознаётся по хэшу.'),
    ('parser', ('парсер', 'parser', 'chromium'),
     'Парсер цен: /products/parser/. Нужны Chromium, ID магазина и настройки. Парсер не изменяет остатки и закупочные цены.'),
    ('incoming', ('поступ', 'приём', 'прием', 'incoming'),
     'Поступления: /incoming; приёмка — /stock-in. Укажите товар, количество и закупочную цену.'),
    ('report', ('отчёт', 'отчет', 'report', 'прибыл'),
     'Отчёт прибыли: /report с датами; Excel — /report/export. Расчёты зависят от закупки, комиссии и доставки.'),
    ('orders', ('заказ', 'orders', 'тапсырыс'),
     'Kaspi-заказы: /orders. Учебные заказы: /management/orders. Их статусы и суммы не смешиваются.'),
]


def local_reference(message):
    message = ' '.join(message.casefold().split())
    for intent, terms, answer in FAQ:
        if any(term in message for term in terms):
            return dict(intent=intent, reply=answer, mode='local_reference', source='docs/integration.md')
    return None
