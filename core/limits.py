# core/limits.py  (você pode criar esse arquivo)
from core.db import get_active_subscription, get_active_trial, get_conn

PLAN_LIMITS = {
    "start": 5,
    "pro": 50,
    "enterprise": 9_999_999,
}

def get_vehicle_limit_for_user(user_id: int) -> int:
    """
    Retorna o limite de veículos do usuário com base no plano ativo.
    Usa a tabela de assinatura / trial.
    """

    # 1) tenta pegar assinatura ativa
    sub = get_active_subscription(user_id)
    plan_name = None

    if sub:
        # ajuste o nome da chave conforme seu código/banco
        # exemplo: sub["plan"], sub["plan_name"], sub["tier"] etc.
        plan_name = (sub.get("plan") or sub.get("plan_name") or "").lower()

    # 2) se não tiver assinatura, ver se está em trial
    if not plan_name:
        trial = get_active_trial(user_id)
        if trial:
            # você pode decidir que trial é sempre igual ao Start, por exemplo
            plan_name = "start"

    # 3) se ainda assim não tiver plano, considera "start" ou "free"
    if not plan_name:
        plan_name = "start"

    # 4) pega o limite pelo dicionário
    limit = PLAN_LIMITS.get(plan_name, PLAN_LIMITS["start"])
    return limit


def get_vehicles_count_for_user(user_id: int) -> int:
    """
    Conta quantos veículos o usuário já cadastrou.
    Ajuste o nome da tabela/colunas conforme o seu banco.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM vehicles WHERE user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    return row[0] if row else 0
