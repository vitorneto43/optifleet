import pandas as pd
from reportlab.pdfgen import canvas

def export_excel(path, rows: list[dict]):
    df = pd.DataFrame(rows)
    df.to_excel(path, index=False)

def export_pdf(path, title: str, rows: list[dict]):
    c = canvas.Canvas(path)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, 800, title)
    c.setFont("Helvetica", 10)
    y = 780
    for r in rows:
        line = " | ".join([f"{k}: {v}" for k,v in r.items()])
        c.drawString(40, y, line[:110])
        y -= 14
        if y < 60:
            c.showPage(); y = 800
    c.save()

def estimate_co2_kg(km: float, km_per_liter: float = 5.0) -> float:
    # diesel ~ 2.68 kg CO2 por litro; 5 km/L => 0.536 kg/km
    return round(km * (2.68 / km_per_liter), 2)

def route_comparison(planned_km, planned_min, exec_km, exec_min):
    return {
        "km_delta": round(exec_km - planned_km, 2),
        "min_delta": round(exec_min - planned_min, 1),
        "overrun_pct": round(((exec_km / planned_km) - 1) * 100, 1) if planned_km else 0.0
    }
