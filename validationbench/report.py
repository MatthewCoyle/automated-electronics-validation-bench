"""Portable JSON evidence and escaped, self-contained HTML reports."""
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import platform
from . import __version__
from .runner import summarize


def write_reports(plan, results, output_dir):
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    report = dict(schema_version=1, mode="SIMULATION ONLY - no physical equipment tested",
                  software_version=__version__, python=platform.python_version(),
                  generated_utc=datetime.now(timezone.utc).isoformat(), plan_sha256=plan.digest,
                  plan=asdict(plan), summary=summarize(results), results=results)
    (dest / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    e = lambda value: escape(str(value), quote=True)
    sections = []
    for result in results:
        failed = [c for c in result["checks"] if not c["passed"]]
        rows = "".join(f"<tr><td>{e(m['time_s'])}</td><td>{e(m['voltage_v'])}</td><td>{e(m['current_a'])}</td><td>{e(round(m['temperature_c'], 5))}</td><td>{e(m['shutdown'])}</td><td>{e(m['reason'])}</td></tr>" for m in result["measurements"])
        sections.append(f"<section><h2>{e(result['scenario'])} <span class='{e(result['status'])}'>{e(result['status'])}</span></h2><p>{e(result['stimulus']['description'])}</p><p>Checks: {len(result['checks'])}; failed: {len(failed)}. Samples: {len(result['measurements'])}.</p><table><tr><th>Time / s</th><th>Voltage / V</th><th>Current / A</th><th>Temperature / C</th><th>Shutdown</th><th>Reason</th></tr>{rows}</table><details><summary>Acceptance checks, errors and recovery evidence</summary><pre>{e(json.dumps(dict(checks=result['checks'], events=result['events'], errors=result['errors']), indent=2))}</pre></details></section>")
    document = """<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Validation evidence</title><style>body{font:16px system-ui,sans-serif;color:#203047;max-width:1100px;margin:40px auto;padding:0 22px}h1,h2{color:#153d60}section{border-top:1px solid #bdcbd5;margin-top:28px}.notice{background:#fff2d6;padding:16px;border-left:5px solid #b47b16}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}td,th{border:1px solid #cad5dd;padding:9px;text-align:left}th{background:#edf3f7}.PASS{color:#176642}.FAIL,.ERROR{color:#af2424}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f7;padding:14px}details{margin:18px 0}footer{margin-top:30px;font-size:13px}</style>"""
    document += f"<h1>{e(plan.name)}</h1><p class='notice'><strong>{e(report['mode'])}</strong>. Generic invented module; these values cannot qualify hardware.</p><p>{e(report['summary'])}</p><p>Plan SHA-256: <code>{e(plan.digest)}</code></p>" + "".join(sections)
    document += f"<footer>Software {e(__version__)}; Python {e(report['python'])}; generated UTC {e(report['generated_utc'])}. Full precision values and complete configuration are in report.json.</footer></html>"
    (dest / "report.html").write_text(document, encoding="utf-8")
    return report["summary"]
