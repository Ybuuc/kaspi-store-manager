"""Report only observations actually tagged by the seller API importer."""
import json
from decimal import Decimal
from io import BytesIO
from datetime import datetime, timezone

from flask import render_template, send_file
from openpyxl import Workbook
from kaspibot.models import KaspiOrderSync, KaspiSyncRun


def verified_rows():
    result=[]
    for row in KaspiOrderSync.query.order_by(KaspiOrderSync.order_code,KaspiOrderSync.id).all():
        try:
            raw=json.loads(row.raw_json or '{}')
        except (ValueError,TypeError):
            continue
        if raw.get('source') != 'kaspi_api':
            continue
        attrs=(raw.get('order') or {}).get('attributes') or {}
        result.append(dict(code=row.order_code,title=row.title,quantity=row.qty,amount=row.sale_sum,
                           state=row.detailed_state or row.state,status=row.detailed_status or row.status_text,
                           date=datetime.fromtimestamp(attrs['creationDate']/1000,timezone.utc).isoformat()
                           if attrs.get('creationDate') else None))
    return result


def render_report(export=False):
    rows=verified_rows()
    run=KaspiSyncRun.query.order_by(KaspiSyncRun.id.desc()).first()
    revenue=sum((Decimal(str(r['amount'])) for r in rows if r['status']=='COMPLETED'),Decimal(0))
    if not export:
        return render_template('kaspibot/real_report.html',rows=rows,run=run,revenue=revenue)
    book=Workbook()
    sheet=book.active
    sheet.title='KaspiOrders'
    sheet.append(['Order','Product','Quantity','Amount KZT','State','Status','Creation UTC'])
    for row in rows:
        sheet.append([row[k] for k in ('code','title','quantity','amount','state','status','date')])
    source=book.create_sheet('Source')
    source.append(['Source','Authenticated Kaspi seller API observations only'])
    source.append(['Completed entries revenue KZT',float(revenue)])
    source.append(['Profit','Not calculated: actual purchase cost, commission and seller delivery fees are not reconciled'])
    source.append(['Last run',run.status if run else 'Not connected'])
    stream=BytesIO()
    book.save(stream)
    stream.seek(0)
    return send_file(stream,download_name='kaspi-real-orders.xlsx',as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def render_onway():
    rows=[row for row in verified_rows() if row['state'] != 'ARCHIVE' and row['status'] not in
          {'COMPLETED','DELIVERED','CANCELLED','CANCELLING','RETURNED','KASPI_DELIVERY_RETURN_REQUESTED'}]
    return render_template('kaspibot/real_onway.html',rows=rows)


def live_summary():
    rows=verified_rows()
    return dict(source='kaspi_api_observations',verified_order_count=len({r['code'] for r in rows}),
                verified_lines=len(rows),stock_total=None,profit=None,
                stockTotal=None,incomingTotal=None,onwayTotal=None,turnoverTotal=None,
                stock_verified=False,message='Остатки, закупка и чистая прибыль не сверены. Показаны только полученные позиции API.')
