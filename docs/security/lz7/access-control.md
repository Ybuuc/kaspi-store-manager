# MVP endpoint қолжетімділігі

| Endpoint | Қонақ | Employee | Admin |
|---|---|---|---|
| `/`, `/login` | Кіру бетіне | Каталогқа | Каталогқа |
| `/products`, `/products/<id>` | Login-ге бағытталады | Оқу | Оқу |
| `/products/new`, `/products/<id>/edit`, `/products/<id>/stock` | Login-ге бағытталады | Оқу және жазу | Оқу және жазу |
| `/admin` | Login-ге бағытталады | 403 | Оқу |

Сервер әр сұрауда пайдаланушыны `users` кестесінен жүктейді. `role` мәні клиент жіберген form немесе session өрісінен алынбайды. Өзгерту формалары CSRF токенін талап етеді. Құпиясөз `scrypt` hash түрінде сақталады; бес қате әрекеттен кейін 15 минутқа блокталады. Session cookie `HttpOnly` және `SameSite=Lax`; HTTPS ортада `KSM_HTTPS=1` арқылы `Secure` қосылады. TLS мен hosting конфигурациясы орналастыру кезінде бөлек тексеріледі.

Тексеру: `tests/test_app.py` ішіндегі login, protected URL, admin direct URL, жалған session role, CSRF, rate limit және cookie configuration тесттері. Бұл MVP құқық матрицасы; рөлді веб арқылы басқару US-17 аясында кейін жасалады.
