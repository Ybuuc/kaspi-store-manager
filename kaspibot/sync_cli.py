import json
import click
from kaspibot.services.kaspi_api import get_orders, KaspiAPIError
from kaspibot.services.seller_sync import synchronize


def register(app):
    @app.cli.command('kaspi-check')
    def check():
        """Read one recent page from the real cabinet without changing the database."""
        try:
            data=get_orders(state='NEW',days_back=1,page_size=1)
            click.echo(json.dumps(dict(connected=True,source='kaspi_api',http_status=200,
                                       page_orders=len(data['data'])),ensure_ascii=False))
        except KaspiAPIError as exc:
            raise click.ClickException(str(exc)) from None

    @app.cli.command('kaspi-sync')
    @click.option('--days',type=click.IntRange(1,366),default=14)
    def sync(days):
        """Read real orders and linked products; never send seller-side mutations."""
        result=synchronize(days)
        click.echo(json.dumps(result,ensure_ascii=False,indent=2))
        if not result['ok']:
            raise click.ClickException('Синхронизация завершилась с ошибками; проверьте журнал и доступ к API.')
