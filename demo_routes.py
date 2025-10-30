# routes/demo_routes.py
from flask import Blueprint, jsonify
from flask_login import login_required, current_user
from core.sim.demo_sim import sim

bp_demo = Blueprint("demo", __name__, url_prefix="/demo")

def _cid(): return int(getattr(current_user, "id", 0) or 0)

@bp_demo.post("/start")
@login_required
def demo_start():
    sim.start(_cid())
    return jsonify(sim.status(_cid()))

@bp_demo.post("/stop")
@login_required
def demo_stop():
    sim.stop(_cid())
    return jsonify({"running": False})

@bp_demo.get("/status")
@login_required
def demo_status():
    return jsonify(sim.status(_cid()))
