# routes/report_routes.py
from flask import Blueprint, send_file, request, jsonify
from core.services.reports import export_excel, export_pdf, estimate_co2_kg, route_comparison
from tempfile import NamedTemporaryFile

bp_reports = Blueprint("reports", __name__)

@bp_reports.post("/api/reports/export")
def export_report():
    payload = request.get_json(force=True)
    rows = payload.get("rows", [])
    fmt = payload.get("format", "excel")
    title = payload.get("title", "Relatório OptiFleet")

    if fmt == "excel":
        with NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            export_excel(f.name, rows)
            return send_file(f.name, as_attachment=True, download_name="relatorio.xlsx")
    elif fmt == "pdf":
        with NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            export_pdf(f.name, title, rows)
            return send_file(f.name, as_attachment=True, download_name="relatorio.pdf")
    return jsonify({"error":"format inválido"}), 400

@bp_reports.post("/api/reports/eco")
def eco_report():
    p = request.get_json(force=True)
    km = float(p.get("km", 0))
    co2 = estimate_co2_kg(km)
    return jsonify({"km": km, "co2_kg": co2})

@bp_reports.post("/api/reports/compare")
def compare_report():
    p = request.get_json(force=True)
    data = route_comparison(
        float(p.get("planned_km",0)), float(p.get("planned_min",0)),
        float(p.get("exec_km",0)), float(p.get("exec_min",0))
    )
    return jsonify(data)
