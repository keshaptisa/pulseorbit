from __future__ import annotations

import io
import csv
from html import escape
import json
import zipfile

from app.models import AssessmentResponse
from app.services.experiment import compare_with_baseline


def build_windows_csv(result: dict) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["start_utc", "end_utc", "severity", "score", "completeness", "adverse_overlap_minutes", "factor_count"])
    for window in result.get("windows", []):
        writer.writerow([
            window["start"], window["end"], window["severity"], window["score"], window["completeness"],
            sum(factor.get("overlap_minutes", 0) for factor in window.get("factors", []) if factor.get("severity") in {"moderate", "high"}),
            len(window.get("factors", [])),
        ])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def build_export(envelope: dict) -> bytes:
    result = envelope["result"]
    sources = result.get("source_status", [])
    source_rows = "".join(
        f"<tr><td>{escape(item['name'])}</td><td>{escape(item['status'])}</td><td>{f'<a href={json.dumps(item.get("url"))}>источник</a>' if item.get('url') else '—'}</td><td><code>{escape(item.get('sha256') or '—')}</code></td></tr>"
        for item in sources
    )
    window_rows = "".join(
        f"<tr><td>{escape(item['start'])}</td><td>{escape(item['end'])}</td><td>{escape(item['severity'])}</td><td>{item['score']}</td><td>{escape(item['completeness'])}</td></tr>"
        for item in result.get("windows", [])
    )
    selected = min(result.get("windows", []), key=lambda item: (item["score"], item["start"]))
    factor_sections = "".join(
        f"<section><h3>{escape(factor['name'])}</h3><p><b>Уровень:</b> {escape(factor['severity'])}; <b>уверенность:</b> {escape(factor['confidence'])}; <b>пересечение:</b> {factor['overlap_minutes']} мин.</p><p>{escape(factor['summary'])}</p><p><b>Механизм:</b> {escape(factor['mechanism'])}</p><p><b>Правило:</b> {escape(factor['applied_rule'])}</p><p><b>Основание уверенности:</b> {escape(factor['confidence_basis'])}</p><p><b>Ограничения:</b> {escape(' '.join(factor['limitations']))}</p>" + "".join(
            f"<div class='evidence'><b>{escape(evidence['title'])}</b> [{escape(evidence['kind'])}]<br>{escape(evidence['value'])} {escape(evidence.get('unit') or '')}<br>Источник: {escape(evidence['source_name'])}; опубликовано: {escape(evidence.get('published_at') or 'не применимо')}; ID: <code>{escape(evidence['record_id'])}</code>{f'<br>SHA-256 записи: <code>{escape(evidence.get("record_sha256"))}</code>' if evidence.get('record_sha256') else ''}{f'<br><a href={json.dumps(evidence.get("source_url"))}>Открыть первоисточник</a>' if evidence.get('source_url') else ''}<br>{escape(evidence['note'])}</div>"
            for evidence in factor.get("evidence", [])
        ) + "</section>"
        for factor in selected.get("factors", [])
    )
    baseline = compare_with_baseline(AssessmentResponse.model_validate(result))
    html = f"""<!doctype html><html lang='ru'><meta charset='utf-8'><title>Расчёт ВКД</title>
<style>body{{font:14px Arial;max-width:1050px;margin:40px auto;color:#172022;line-height:1.45}}h1,h2,h3{{margin-top:28px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;border:1px solid #ccd5d2;text-align:left;vertical-align:top}}th{{background:#eef2f1}}code{{font-size:11px;overflow-wrap:anywhere}}.notice,.evidence{{padding:10px 12px;margin:10px 0;background:#f4f7f6;border-left:3px solid #087f5b}}pre{{white-space:pre-wrap}}</style>
<h1>Расчёт окна ВКД</h1><p><b>ID:</b> {escape(envelope['result_id'])}; <b>алгоритм:</b> {escape(result['algorithm_version'])}</p><p><b>Операторский статус:</b> {escape(result['operational_status'])}</p><p><b>Обязательное действие:</b> {escape(result['operator_action'])}</p><p><b>Рекомендация:</b> {escape(result['recommendation'])}</p>
<p class='notice'>{escape(result['data_notice'])}</p><p><b>Baseline:</b> {escape(baseline['baseline']['start'])}, индекс {baseline['baseline']['score']}; <b>выбрано:</b> {escape(baseline['selected']['start'])}, индекс {baseline['selected']['score']}; изменение {baseline['score_improvement']}.</p>
<h2>Домены риска</h2><pre>{escape(json.dumps(result['risk_domains'], ensure_ascii=False, indent=2))}</pre>
<h2>Policy gates</h2><p><b>Уверенность решения:</b> {escape(result['decision_confidence'])}</p><pre>{escape(json.dumps(result['decision_gates'], ensure_ascii=False, indent=2))}</pre>
<h2>Параметры и режим</h2><pre>{escape(json.dumps(result['request'], ensure_ascii=False, indent=2))}</pre><p><b>Cutoff:</b> {escape(result.get('historical_cutoff') or 'не применяется')}; <b>режим:</b> {escape(result['evaluation_mode'])}</p>
<h2>Траектория</h2><pre>{escape(json.dumps(result['trajectory'], ensure_ascii=False, indent=2))}</pre>
<h2>Последующая проверка</h2><pre>{escape(json.dumps(result.get('verification'), ensure_ascii=False, indent=2))}</pre>
<h2>Источники</h2><table><tr><th>Источник</th><th>Статус</th><th>URL</th><th>SHA-256</th></tr>{source_rows}</table>
<h2>Сравнение окон</h2><table><tr><th>Начало UTC</th><th>Окончание UTC</th><th>Уровень</th><th>Индекс</th><th>Полнота</th></tr>{window_rows}</table>
<h2>Факторы выбранного окна</h2>{factor_sections}
<p class='notice'>Индекс является порядковой эвристикой поддержки решения, а не вероятностью травмы или допуском к ВКД.</p></html>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("result.json", json.dumps(envelope, ensure_ascii=False, indent=2))
        archive.writestr("windows.csv", build_windows_csv(result))
        archive.writestr("report.html", html)
        archive.writestr("manifest.json", json.dumps({"result_id": envelope["result_id"], "content_sha256": envelope["content_sha256"], "algorithm_version": result["algorithm_version"], "sources": sources}, ensure_ascii=False, indent=2))
    return buffer.getvalue()
