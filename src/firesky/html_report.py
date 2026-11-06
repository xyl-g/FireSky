from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable, Optional


SERIES = (
    ("burn_score", "存在", "#d94b32"),
    ("intensity_score", "强度", "#e89320"),
    ("extent_score", "范围", "#287f6b"),
    ("vividness_score", "鲜艳", "#3567a8"),
)


def write_html_report(report, output: Optional[str | Path] = None) -> Path:
    target = resolve_html_report_path(report, output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html_report(report), encoding="utf-8")
    return target.resolve()


def resolve_html_report_path(report, output: Optional[str | Path] = None) -> Path:
    if output is not None:
        requested = Path(output).expanduser()
        if requested.suffix.lower() == ".html":
            return requested
        directory = requested
    else:
        directory = Path("reports")
    directory = directory / report.target_date.isoformat() / report.phenomenon
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    coordinates = f"{getattr(report, 'observer_name', 'location')}-{report.target_date.isoformat()}"
    slug = slugify(coordinates) or "forecast"
    return directory / f"{slug}-{report.phenomenon}-{report.model_source}-{stamp}.html"


def slugify(value: str) -> str:
    cleaned = "".join(character.lower() if character.isascii() and character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part)[:72]


def render_html_report(report) -> str:
    phenomenon = "朝霞" if report.phenomenon == "dawn" else "晚霞"
    event_label = "日出" if report.phenomenon == "dawn" else "日落"
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    score_label, score_tone = score_assessment(report.burn_score)
    if getattr(report, "forecast_mode", "model") == "nowcast" and report.nowcast is not None:
        metrics = (
            ("存在", report.burn_score, score_tone),
            ("强度", report.intensity_score, "amber"),
            ("鲜艳度", report.vividness_score, "blue"),
            ("云幕", report.nowcast.cloud_blanket_score, "neutral"),
            ("云纹理", report.nowcast.cloud_texture_score, "green"),
            ("地平线通透", report.nowcast.horizon_clearance_score, "neutral"),
        )
    else:
        confidence_label = (
            "输入覆盖诊断" if report.model_source == "ec" else "置信度"
        )
        metrics = (
            ("存在", report.burn_score, score_tone),
            ("强度", report.intensity_score, "amber"),
            ("大面积成霞", report.extent_score, "green"),
            ("鲜艳度", report.vividness_score, "blue"),
            (confidence_label, report.confidence_score, "neutral"),
            ("模式分歧", report.model_disagreement_score, "neutral"),
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(report.observer_name)} {report.target_date.isoformat()} {phenomenon}预测</title>
  <style>
    :root {{ color-scheme: light; --ink:#182027; --muted:#667078; --line:#d8dde0; --paper:#f6f7f5; --white:#fff; --red:#d94b32; --amber:#e89320; --green:#287f6b; --blue:#3567a8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--paper); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; letter-spacing:0; }}
    header {{ background:#20282d; color:#fff; border-bottom:5px solid var(--amber); }}
    .wrap {{ width:min(1180px, calc(100% - 32px)); margin:0 auto; }}
    .head {{ min-height:230px; padding:34px 0 28px; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:28px; align-items:end; }}
    .eyebrow {{ color:#d6dcdf; font-size:13px; margin:0 0 12px; }}
    h1 {{ margin:0; font-size:clamp(30px,5vw,54px); line-height:1.08; font-weight:720; letter-spacing:0; }}
    .subtitle {{ margin:14px 0 0; color:#e7ebed; font-size:16px; line-height:1.7; }}
    .verdict {{ min-width:190px; padding-left:24px; border-left:1px solid #596268; }}
    .verdict strong {{ display:block; font-size:52px; line-height:1; color:#ffb13b; }}
    .verdict span {{ display:block; margin-top:8px; color:#e7ebed; }}
    main section {{ padding:30px 0; border-bottom:1px solid var(--line); }}
    h2 {{ margin:0 0 18px; font-size:20px; letter-spacing:0; }}
    .facts {{ display:flex; flex-wrap:wrap; gap:10px 24px; color:var(--muted); font-size:14px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; margin-top:22px; }}
    .metric {{ background:var(--white); border:1px solid var(--line); border-radius:6px; padding:15px; min-height:104px; }}
    .metric span {{ display:block; color:var(--muted); font-size:13px; }}
    .metric strong {{ display:block; margin-top:12px; font-size:29px; line-height:1; }}
    .metric.red strong {{ color:var(--red); }} .metric.amber strong {{ color:#a75c00; }} .metric.green strong {{ color:var(--green); }} .metric.blue strong {{ color:var(--blue); }}
    .summary {{ display:grid; grid-template-columns:1fr 1fr; gap:12px 32px; margin:0; padding:0; list-style:none; }}
    .summary li {{ padding:0 0 12px 18px; border-bottom:1px solid var(--line); line-height:1.65; position:relative; }}
    .summary li::before {{ content:""; width:7px; height:7px; background:var(--amber); position:absolute; left:0; top:.62em; }}
    .chart-shell {{ background:var(--white); border:1px solid var(--line); border-radius:6px; padding:14px; overflow:hidden; }}
    .chart-shell svg {{ display:block; width:100%; height:auto; aspect-ratio:4 / 1.25; min-height:240px; }}
    .legend {{ display:flex; flex-wrap:wrap; gap:14px; margin:0 0 8px; font-size:13px; color:var(--muted); }}
    .legend i {{ display:inline-block; width:12px; height:3px; margin-right:6px; vertical-align:middle; }}
    .table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:6px; background:var(--white); }}
    table {{ width:100%; border-collapse:collapse; min-width:790px; font-variant-numeric:tabular-nums; }}
    th,td {{ padding:12px 14px; text-align:right; border-bottom:1px solid var(--line); white-space:nowrap; }}
    th:first-child,td:first-child {{ text-align:left; }} th {{ background:#eef1f1; color:#4f5960; font-size:12px; }} td {{ font-size:14px; }}
    tr.best td {{ background:#fff7e9; }} tr:last-child td {{ border-bottom:0; }}
    .diagnostics {{ display:grid; gap:8px; }}
    details {{ background:var(--white); border:1px solid var(--line); border-radius:6px; }}
    summary {{ cursor:pointer; padding:14px 16px; font-weight:650; }}
    details ul {{ margin:0; padding:0 28px 16px 38px; color:#49535a; line-height:1.65; }}
    footer {{ padding:22px 0 34px; color:var(--muted); font-size:12px; }}
    @media (max-width:900px) {{ .metrics {{ grid-template-columns:repeat(3,1fr); }} .summary {{ grid-template-columns:1fr; }} }}
    @media (max-width:620px) {{ .wrap {{ width:min(100% - 20px,1180px); }} .head {{ min-height:0; grid-template-columns:1fr; padding:24px 0; }} .verdict {{ border-left:0; border-top:1px solid #596268; padding:18px 0 0; }} .metrics {{ grid-template-columns:repeat(2,1fr); }} .metric {{ min-height:94px; }} .chart-shell {{ padding:8px; }} main section {{ padding:24px 0; }} }}
  </style>
</head>
<body>
  <header><div class="wrap head"><div><p class="eyebrow">{escape(report.target_date.isoformat())} · {escape(report.timezone_name)} · {escape(model_name(report.model_source))}</p><h1>{escape(report.observer_name)} {phenomenon}预测</h1><p class="subtitle">{event_label} {report.anchor_local.strftime('%H:%M %Z')} · 最佳时刻 {format_time(report.best_time_local)} · 弱霞光潜力 {report.potential_duration_minutes} 分钟</p></div><div class="verdict"><strong>{report.burn_score * 100:.0f}</strong><span>{score_label} · 存在分数 / 100</span></div></div></header>
  <main>
    <section><div class="wrap"><div class="facts"><span>生成时间 {escape(generated)}</span><span>AOD550 {report.aerosol.aod550:.2f}</span><span>气溶胶 {escape(report.aerosol.quality)}</span><span>历史记录 {report.history_record_id if report.history_record_id is not None else '关闭'}</span></div><div class="metrics">{metric_cards(metrics)}</div></div></section>
    <section><div class="wrap"><h2>结论摘要</h2><ul class="summary">{list_items(report.summary)}</ul></div></section>
    <section><div class="wrap"><h2>每 5 分钟趋势</h2><div class="chart-shell"><div class="legend">{chart_legend()}</div>{score_chart(report.steps, report.best_time_local)}</div></div></section>
    <section><div class="wrap"><h2>逐时评分</h2><div class="table-wrap">{step_table(report.steps, report.best_time_local)}</div></div></section>
    <section><div class="wrap"><h2>诊断明细</h2><div class="diagnostics">{diagnostic_details(report.steps)}</div></div></section>
  </main>
  <footer><div class="wrap">firesky · 数值模式预测存在误差，请结合临近卫星云图与现场天气判断。</div></footer>
</body>
</html>"""


def metric_cards(metrics: Iterable[tuple[str, float, str]]) -> str:
    return "".join(
        f'<div class="metric {tone}"><span>{escape(label)}</span><strong>{value * 100:.0f}</strong></div>'
        for label, value, tone in metrics
    )


def score_chart(steps, best_time) -> str:
    if not steps:
        return '<p>无可用逐时结果</p>'
    width, height = 1000, 280
    left, right, top, bottom = 48, 18, 18, 42
    plot_width, plot_height = width - left - right, height - top - bottom
    x_at = lambda index: left + (plot_width * index / max(1, len(steps) - 1))
    y_at = lambda value: top + plot_height * (1.0 - value)
    grid = "".join(
        f'<line x1="{left}" y1="{y_at(value):.1f}" x2="{width-right}" y2="{y_at(value):.1f}" stroke="#dfe3e4"/><text x="{left-8}" y="{y_at(value)+4:.1f}" text-anchor="end" fill="#737d83" font-size="11">{int(value*100)}</text>'
        for value in (0.0, 0.25, 0.5, 0.75, 1.0)
    )
    polylines = "".join(
        f'<polyline points="{" ".join(f"{x_at(index):.1f},{y_at(getattr(step, attribute)):.1f}" for index, step in enumerate(steps))}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
        for attribute, _, color in SERIES
    )
    label_indexes = sorted({0, len(steps) - 1, *(range(2, len(steps), 3))})
    labels = "".join(
        f'<text x="{x_at(index):.1f}" y="{height-15}" text-anchor="middle" fill="#667078" font-size="11">{steps[index].valid_time_local.strftime("%H:%M")}</text>'
        for index in label_indexes
    )
    best_line = ""
    if best_time is not None:
        best_index = min(range(len(steps)), key=lambda index: abs((steps[index].valid_time_local - best_time).total_seconds()))
        best_line = f'<line x1="{x_at(best_index):.1f}" y1="{top}" x2="{x_at(best_index):.1f}" y2="{height-bottom}" stroke="#20282d" stroke-width="1" stroke-dasharray="5 5"/>'
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="逐时评分趋势图">{grid}{best_line}{polylines}{labels}</svg>'


def chart_legend() -> str:
    return "".join(f'<span><i style="background:{color}"></i>{label}</span>' for _, label, color in SERIES)


def step_table(steps, best_time) -> str:
    rows = []
    for step in steps:
        is_best = best_time is not None and step.valid_time_local == best_time
        cloud = step.top_candidate.cloud_type if step.top_candidate else "无明确目标"
        rows.append(
            f'<tr class="{"best" if is_best else ""}"><td>{step.valid_time_local.strftime("%H:%M %Z")}</td><td>{step.solar_elevation_deg:.1f}°</td><td>{step.burn_score*100:.0f}</td><td>{step.intensity_score*100:.0f}</td><td>{step.extent_score*100:.0f}</td><td>{step.vividness_score*100:.0f}</td><td>{step.visibility_score*100:.0f}</td><td>{escape(cloud)}</td><td>{step.burning_cloud_count}</td></tr>'
        )
    return '<table><thead><tr><th>时间</th><th>太阳高度</th><th>存在</th><th>强度</th><th>范围</th><th>鲜艳</th><th>通视</th><th>目标云</th><th>候选格</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'


def diagnostic_details(steps) -> str:
    return "".join(
        f'<details><summary>{step.valid_time_local.strftime("%H:%M %Z")} · 存在 {step.burn_score*100:.0f} · {escape(step.top_candidate.cloud_type if step.top_candidate else "无明确目标云")}</summary><ul>{list_items(step.reasons)}</ul></details>'
        for step in steps
    )


def list_items(values: Iterable[str]) -> str:
    return "".join(f"<li>{escape(str(value))}</li>" for value in values)


def format_time(value) -> str:
    return value.strftime("%H:%M %Z") if value is not None else "无"


def model_name(value: str) -> str:
    return {
        "gfs": "GFS",
        "ec": "ECMWF",
        "satellite": "卫星实况",
    }.get(value, value.upper())


def score_assessment(value: float) -> tuple[str, str]:
    if value >= 0.70:
        return "质量较高", "red"
    if value >= 0.50:
        return "值得关注", "amber"
    if value >= 0.30:
        return "存在潜力", "green"
    return "条件偏弱", "blue"
