# IA Logística — Rotas com Trânsito, Pedágios, Janelas de Horário, Múltiplos Veículos e Manutenção

Este projeto fornece:
- **Rotas reais com curvas (vias)** usando Google Directions/Distance Matrix.
- **Trânsito em tempo real** (quando suportado pela API).
- **Obras/incidentes**: incorporados indiretamente via duração no trânsito (ou plugável por provedor).
- **Pedágios**: custo estimado por rota (sujeito a cobertura | você pode plugar provedores ou matriz própria).
- **Múltiplos veículos** e **janelas de horário** (VRPTW) com **OR-Tools**.
- **Previsão de manutenção** básica por ML (ex.: risco de falha nos próximos X dias).

## Como rodar (API Flask)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate | Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edite .env e coloque sua GOOGLE_MAPS_API_KEY

python app.py
# API em http://127.0.0.1:5000
