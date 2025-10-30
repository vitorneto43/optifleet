# routes/contact_routes.py
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash

bp_contact = Blueprint("contact", __name__)  # sem url_prefix

@bp_contact.get("/contact")
def contact_page():
    return render_template("contact.html", today=date.today().strftime("%d/%m/%Y"))

@bp_contact.post("/contact")
def contact_submit():
    data = {
        "name": request.form.get("name","").strip(),
        "email": request.form.get("email","").strip(),
        "company": request.form.get("company","").strip(),
        "message": request.form.get("message","").strip(),
        "ip": request.remote_addr,
    }
    print("[CONTACT] recebido:", data)
    flash("Recebemos sua mensagem. Em breve entraremos em contato.", "success")
    return redirect(url_for("contact.contact_page"))

