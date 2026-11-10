//! Existing report HTML, charts and escaping, without a Python renderer.
use chrono::{DateTime, FixedOffset};
use serde::Deserialize;
use serde_json::{json, Value};
use std::fmt::Write as _;

const SERIES: [(&str, &str, &str); 4] = [
    ("burn_score", "存在", "#d94b32"),
    ("intensity_score", "强度", "#e89320"),
    ("extent_score", "范围", "#287f6b"),
    ("vividness_score", "鲜艳", "#3567a8"),
];
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    report: Value,
    generated_local: Option<String>,
}
pub fn execute(params: Params) -> Result<Value, String> {
    let generated = params
        .generated_local
        .map(Ok)
        .unwrap_or_else(host_generated_label)?;
    Ok(json!({"contract":"rust-html-report-v1","html":render(&params.report, &generated)?}))
}
fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
    value[key]
        .as_str()
        .ok_or_else(|| format!("HTML report missing string {key}"))
}
fn number(value: &Value, key: &str) -> Result<f64, String> {
    value[key]
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(|| format!("HTML report missing finite {key}"))
}
fn escape(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}
fn items(value: &Value) -> Result<String, String> {
    value
        .as_array()
        .ok_or("HTML report list required")?
        .iter()
        .map(|v| {
            v.as_str()
                .map(|v| format!("<li>{}</li>", escape(v)))
                .ok_or("HTML report text item required".into())
        })
        .collect()
}
fn time(value: &Value, key: &str) -> Result<DateTime<FixedOffset>, String> {
    crate::time_contract::parse_zoned(text(value, key)?)
}
fn clock(time: DateTime<FixedOffset>, zone: chrono_tz::Tz, short: bool) -> String {
    time.with_timezone(&zone)
        .format(if short { "%H:%M" } else { "%H:%M %Z" })
        .to_string()
}

pub(crate) fn host_generated_label() -> Result<String, String> {
    #[cfg(unix)]
    {
        let now: DateTime<chrono::Utc> = std::time::SystemTime::now().into();
        let seconds =
            libc::time_t::try_from(now.timestamp()).map_err(|_| "HTML host timestamp overflow")?;
        let mut local: libc::tm = unsafe { std::mem::zeroed() };
        if unsafe { libc::localtime_r(&seconds, &mut local) }.is_null() {
            return Err("cannot resolve host-local HTML timestamp".into());
        }
        let mut bytes = [0u8; 128];
        let size = unsafe {
            libc::strftime(
                bytes.as_mut_ptr().cast(),
                bytes.len(),
                c"%Y-%m-%d %H:%M %Z".as_ptr(),
                &local,
            )
        };
        if size == 0 {
            return Err("cannot format host-local HTML timestamp".into());
        }
        String::from_utf8(bytes[..size].to_vec()).map_err(|_| "invalid host-local timestamp".into())
    }
    #[cfg(not(unix))]
    Err("host-local HTML timestamp needs an explicit generated_local on this platform".into())
}

pub(crate) fn render(report: &Value, generated: &str) -> Result<String, String> {
    if generated.len() > 256 || generated.chars().any(char::is_control) {
        return Err("invalid generated HTML timestamp label".into());
    }
    let zone: chrono_tz::Tz = text(report, "timezone_name")?
        .parse()
        .map_err(|_| "invalid HTML timezone")?;
    let date = text(report, "target_date")?;
    crate::time_contract::parse_date(date)?;
    let phenomenon = text(report, "phenomenon")?;
    let (phenomenon, event_label) = match phenomenon {
        "dawn" => ("朝霞", "日出"),
        "dusk" => ("晚霞", "日落"),
        _ => return Err("HTML report needs dawn or dusk".into()),
    };
    let observer = escape(text(report, "observer_name")?);
    let model = text(report, "model_source")?;
    let model_name = match model {
        "gfs" => "GFS".into(),
        "ec" => "ECMWF".into(),
        "satellite" => "卫星实况".into(),
        other => other.to_uppercase(),
    };
    let score = number(report, "burn_score")?;
    let (score_label, tone) = if score >= 0.70 {
        ("质量较高", "red")
    } else if score >= 0.50 {
        ("值得关注", "amber")
    } else if score >= 0.30 {
        ("存在潜力", "green")
    } else {
        ("条件偏弱", "blue")
    };
    let nowcast = report["forecast_mode"] == "nowcast" && !report["nowcast"].is_null();
    let metrics = if nowcast {
        vec![
            ("存在", score, tone),
            ("强度", number(report, "intensity_score")?, "amber"),
            ("鲜艳度", number(report, "vividness_score")?, "blue"),
            (
                "云幕",
                number(&report["nowcast"], "cloud_blanket_score")?,
                "neutral",
            ),
            (
                "云纹理",
                number(&report["nowcast"], "cloud_texture_score")?,
                "green",
            ),
            (
                "地平线通透",
                number(&report["nowcast"], "horizon_clearance_score")?,
                "neutral",
            ),
        ]
    } else {
        vec![
            ("存在", score, tone),
            ("强度", number(report, "intensity_score")?, "amber"),
            ("大面积成霞", number(report, "extent_score")?, "green"),
            ("鲜艳度", number(report, "vividness_score")?, "blue"),
            (
                if model == "ec" {
                    "输入覆盖诊断"
                } else {
                    "置信度"
                },
                number(report, "confidence_score")?,
                "neutral",
            ),
            (
                "模式分歧",
                number(report, "model_disagreement_score")?,
                "neutral",
            ),
        ]
    };
    let cards: String = metrics
        .iter()
        .map(|(label, value, tone)| {
            format!(
                "<div class=\"metric {tone}\"><span>{label}</span><strong>{:.0}</strong></div>",
                value * 100.0
            )
        })
        .collect();
    let steps = report["steps"]
        .as_array()
        .filter(|v| v.len() <= 4096)
        .ok_or("HTML report needs bounded steps")?;
    let times = steps
        .iter()
        .map(|s| time(s, "valid_time_local"))
        .collect::<Result<Vec<_>, _>>()?;
    let best = if report["best_time_local"].is_null() {
        None
    } else {
        Some(time(report, "best_time_local")?)
    };
    let mut table_rows = String::new();
    let mut details = String::new();
    for (step, &date) in steps.iter().zip(&times) {
        let label = clock(date, zone, false);
        let candidate = if step["top_candidate"].is_null() {
            None
        } else {
            Some(text(&step["top_candidate"], "cloud_type")?)
        };
        let cloud = escape(candidate.unwrap_or("无明确目标"));
        let class = if best.is_some_and(|best| wall_time(best, zone) == wall_time(date, zone)) {
            "best"
        } else {
            ""
        };
        let solar = number(step, "solar_elevation_deg")?;
        let burn = number(step, "burn_score")? * 100.0;
        let intensity = number(step, "intensity_score")? * 100.0;
        let extent = number(step, "extent_score")? * 100.0;
        let vividness = number(step, "vividness_score")? * 100.0;
        let visibility = number(step, "visibility_score")? * 100.0;
        let count = step["burning_cloud_count"]
            .as_u64()
            .ok_or("invalid HTML cloud count")?;
        write!(table_rows, "<tr class=\"{class}\"><td>{label}</td><td>{solar:.1}°</td><td>{burn:.0}</td><td>{intensity:.0}</td><td>{extent:.0}</td><td>{vividness:.0}</td><td>{visibility:.0}</td><td>{cloud}</td><td>{count}</td></tr>").unwrap();
        let detail_cloud = escape(candidate.unwrap_or("无明确目标云"));
        write!(details,"<details><summary>{label} · 存在 {burn:.0} · {detail_cloud}</summary><ul>{}</ul></details>",items(&step["reasons"])?).unwrap();
    }
    let chart = chart(steps, &times, best, zone)?;
    let legend: String = SERIES
        .iter()
        .map(|(_, label, color)| {
            format!("<span><i style=\"background:{color}\"></i>{label}</span>")
        })
        .collect();
    let anchor = clock(time(report, "anchor_local")?, zone, false);
    let best = best
        .map(|t| clock(t, zone, false))
        .unwrap_or_else(|| "无".into());
    let duration = if report["potential_duration_minutes"].is_null() {
        "None".to_owned()
    } else {
        report["potential_duration_minutes"]
            .as_i64()
            .ok_or("invalid HTML duration")?
            .to_string()
    };
    let aod = number(&report["aerosol"], "aod550")?;
    let quality = escape(text(&report["aerosol"], "quality")?);
    let summary = items(&report["summary"])?;
    let generated = escape(generated);
    let zone_name = escape(text(report, "timezone_name")?);
    let model_name = escape(&model_name);
    let score = score * 100.0;
    let styles = include_str!("report.css");
    Ok(format!(
        r#"<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{observer} {date} {phenomenon}预测</title>
  <style>
{styles}  </style>
</head>
<body>
  <header><div class="wrap head"><div><p class="eyebrow">{date} · {zone_name} · {model_name}</p><h1>{observer} {phenomenon}预测</h1><p class="subtitle">{event_label} {anchor} · 最佳时刻 {best} · 弱霞光潜力 {duration} 分钟</p></div><div class="verdict"><strong>{score:.0}</strong><span>{score_label} · 存在分数 / 100</span></div></div></header>
  <main>
    <section><div class="wrap"><div class="facts"><span>生成时间 {generated}</span><span>AOD550 {aod:.2}</span><span>气溶胶 {quality}</span></div><div class="metrics">{cards}</div></div></section>
    <section><div class="wrap"><h2>结论摘要</h2><ul class="summary">{summary}</ul></div></section>
    <section><div class="wrap"><h2>每 5 分钟趋势</h2><div class="chart-shell"><div class="legend">{legend}</div>{chart}</div></div></section>
    <section><div class="wrap"><h2>逐时评分</h2><div class="table-wrap"><table><thead><tr><th>时间</th><th>太阳高度</th><th>存在</th><th>强度</th><th>范围</th><th>鲜艳</th><th>通视</th><th>目标云</th><th>候选格</th></tr></thead><tbody>{table_rows}</tbody></table></div></div></section>
    <section><div class="wrap"><h2>诊断明细</h2><div class="diagnostics">{details}</div></div></section>
  </main>
  <footer><div class="wrap">firesky · 数值模式预测存在误差，请结合临近卫星云图与现场天气判断。</div></footer>
</body>
</html>"#
    ))
}

fn chart(
    steps: &[Value],
    times: &[DateTime<FixedOffset>],
    best: Option<DateTime<FixedOffset>>,
    zone: chrono_tz::Tz,
) -> Result<String, String> {
    if steps.is_empty() {
        return Ok("<p>无可用逐时结果</p>".into());
    }
    let x = |index: usize| 48.0 + 934.0 * index as f64 / (steps.len() - 1).max(1) as f64;
    let y = |value: f64| 18.0 + 220.0 * (1.0 - value);
    let mut grid = String::new();
    for value in [0.0, 0.25, 0.5, 0.75, 1.0] {
        write!(grid,"<line x1=\"48\" y1=\"{:.1}\" x2=\"982\" y2=\"{:.1}\" stroke=\"#dfe3e4\"/><text x=\"40\" y=\"{:.1}\" text-anchor=\"end\" fill=\"#737d83\" font-size=\"11\">{}</text>",y(value),y(value),y(value)+4.0,(value*100.0) as i32).unwrap();
    }
    let best_line = if let Some(best) = best {
        let index = times
            .iter()
            .enumerate()
            .min_by_key(|(_, t)| {
                wall_time(**t, zone)
                    .signed_duration_since(wall_time(best, zone))
                    .abs()
            })
            .unwrap()
            .0;
        format!("<line x1=\"{:.1}\" y1=\"18\" x2=\"{:.1}\" y2=\"238\" stroke=\"#20282d\" stroke-width=\"1\" stroke-dasharray=\"5 5\"/>",x(index),x(index))
    } else {
        String::new()
    };
    let mut lines = String::new();
    for (attribute, _, color) in SERIES {
        let points = steps
            .iter()
            .enumerate()
            .map(|(i, s)| Ok(format!("{:.1},{:.1}", x(i), y(number(s, attribute)?))))
            .collect::<Result<Vec<_>, String>>()?
            .join(" ");
        write!(lines,"<polyline points=\"{points}\" fill=\"none\" stroke=\"{color}\" stroke-width=\"3\" stroke-linejoin=\"round\" stroke-linecap=\"round\"/>").unwrap();
    }
    let mut indices = std::collections::BTreeSet::from([0, steps.len() - 1]);
    indices.extend((2..steps.len()).step_by(3));
    let labels: String = indices.into_iter().map(|i| format!("<text x=\"{:.1}\" y=\"265\" text-anchor=\"middle\" fill=\"#667078\" font-size=\"11\">{}</text>",x(i),clock(times[i],zone,true))).collect();
    Ok(format!("<svg viewBox=\"0 0 1000 280\" role=\"img\" aria-label=\"逐时评分趋势图\">{grid}{best_line}{lines}{labels}</svg>"))
}

// Python's owning report compares same-zone datetimes in local wall time,
// including fold-zero/fold-one equality at a daylight-saving rollback.
fn wall_time(time: DateTime<FixedOffset>, zone: chrono_tz::Tz) -> chrono::NaiveDateTime {
    time.with_timezone(&zone).naive_local()
}
