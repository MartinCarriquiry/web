# finanzas_app.py
# ---------------------------------------------------------
# Finanzas personales con Streamlit + Supabase
# - Cliente de Supabase por sesión (NO cache global)
# - Limpieza de tokens en URL (evita logueos cruzados)
# - Login/Registro
# - Caché de datos por usuario (clave = user_id)
# - Admin de categorías y movimientos
# - KPIs y gráfico de balance
# ---------------------------------------------------------

from datetime import date
import uuid

import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client

# ------------------ CONFIG ------------------
st.set_page_config(page_title="Finanzas personales", page_icon="💸", layout="wide")

# 0) Si alguien abre la app con tokens en la URL (p. ej. desde un mail de verificación),
#    los limpiamos para que NO se comparta la sesión al reenviar el link.
try:
    params = st.experimental_get_query_params()
    if any(k in params for k in ("access_token", "refresh_token")):
        st.experimental_set_query_params()  # limpia el querystring
except Exception:
    pass

# 1) Generamos un ID por sesión de navegador (ayuda a aislar estado)
if "_sid" not in st.session_state:
    st.session_state["_sid"] = str(uuid.uuid4())

# ------------------ SUPABASE (un cliente por sesión) ------------------
def get_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_ANON_KEY"]
    # un cliente por sesión del navegador (NO global, NO cacheado)
    if "supa" not in st.session_state:
        st.session_state["supa"] = create_client(url, key)
    return st.session_state["supa"]

# ------------------ CACHÉS ------------------
def clear_data_caches():
    try:
        load_transactions.clear()  # type: ignore
        load_categories.clear()    # type: ignore
    except Exception:
        pass

def current_user():
    """Devuelve el usuario actual y limpia cachés si cambió de identidad."""
    supa = get_supabase()
    u = supa.auth.get_user()
    uid = u.user.id if u and u.user else None
    if uid != st.session_state.get("sb_uid"):
        st.session_state["sb_uid"] = uid
        clear_data_caches()
    return u.user if u and u.user else None

# ------------------ AUTH UI ------------------
def auth_view():
    supa = get_supabase()
    st.title("💸 Finanzas — Iniciar sesión")

    tab_login, tab_signup = st.tabs(["🔐 Ingresar", "🆕 Registrarme"])

    with tab_login:
        email = st.text_input("Email", key="login_email")
        pwd   = st.text_input("Contraseña", type="password", key="login_pwd")
        if st.button("Ingresar", use_container_width=True):
            try:
                supa.auth.sign_in_with_password({"email": email, "password": pwd})
                # refrescar cliente por si cambió el token
                st.session_state.pop("supa", None)
                st.experimental_rerun()
            except Exception:
                st.error("No pudimos iniciar sesión. Revisá email/contraseña.")

    with tab_signup:
        email2 = st.text_input("Email", key="signup_email")
        pwd2   = st.text_input("Contraseña", type="password", key="signup_pwd")
        if st.button("Crear cuenta", use_container_width=True):
            try:
                supa.auth.sign_up({"email": email2, "password": pwd2})
                st.success("Revisá tu email para confirmar la cuenta. Luego volvé e iniciá sesión.")
            except Exception:
                st.error("No pudimos registrar. Probá con otro email.")

# ------------------ DATA LAYER ------------------
@st.cache_data(ttl=30)
def load_categories(user_id: str) -> pd.DataFrame:
    supa = get_supabase()
    res = supa.table("categories").select("*").eq("user_id", user_id).order("name").execute()
    return pd.DataFrame(res.data or [], columns=["id", "user_id", "name", "kind"])

@st.cache_data(ttl=30)
def load_transactions(user_id: str) -> pd.DataFrame:
    supa = get_supabase()
    res = supa.table("transactions").select("*").eq("user_id", user_id).order("tdate").execute()
    df = pd.DataFrame(res.data or [], columns=["id","user_id","tdate","amount","kind","category","note"])
    if not df.empty:
        df["tdate"] = pd.to_datetime(df["tdate"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    return df

def ensure_default_categories(user_id: str):
    """Crea categorías básicas si el usuario no tiene ninguna."""
    cat_df = load_categories(user_id)
    if not cat_df.empty:
        return
    supa = get_supabase()
    defaults = [
        ("Sueldo", "ingreso"), ("Extra", "ingreso"),
        ("Alquiler", "gasto"), ("Comida", "gasto"), ("Transporte", "gasto"),
        ("Servicios", "gasto"), ("Entretenimiento", "gasto"),
        ("Salud", "gasto"), ("Educación", "gasto"),
        ("Ahorro/ETF", "inversion"), ("Cripto", "inversion"), ("Plazo Fijo", "inversion"),
    ]
    for name, kind in defaults:
        try:
            supa.table("categories").insert({"user_id": user_id, "name": name, "kind": kind}).execute()
        except Exception:
            pass
    clear_data_caches()

def add_category(user_id: str, name: str, kind: str):
    get_supabase().table("categories").insert(
        {"user_id": user_id, "name": name.strip(), "kind": kind}
    ).execute()
    clear_data_caches()

def delete_category(user_id: str, name: str, reassign_to: str | None):
    supa = get_supabase()
    # Reasignar movimientos si corresponde
    if reassign_to:
        supa.table("transactions").update({"category": reassign_to}) \
            .eq("user_id", user_id).eq("category", name).execute()
    # Borrar
    supa.table("categories").delete().eq("user_id", user_id).eq("name", name).execute()
    clear_data_caches()

def add_transaction(user_id: str, tdate: date, amount: float, kind: str, category: str, note: str):
    get_supabase().table("transactions").insert({
        "user_id": user_id,
        "tdate": tdate.isoformat(),
        "amount": float(amount),
        "kind": kind,
        "category": category,
        "note": note or "",
    }).execute()
    clear_data_caches()

# ------------------ APP (post-login) ------------------
def app_view(user):
    user_id = user.id
    ensure_default_categories(user_id)

    # ---- Sidebar: sesión y logout ----
    st.sidebar.success(f"Sesión: {user.email}")
    if st.sidebar.button("Cerrar sesión", use_container_width=True):
        supa = get_supabase()
        try:
            supa.auth.sign_out()
        except Exception:
            pass
        # limpiar TODO lo ligado a la sesión
        for k in ["supa", "user", "sb_session", "sb_uid"]:
            st.session_state.pop(k, None)
        clear_data_caches()
        st.experimental_rerun()

    # ---- Sidebar: alta de movimiento ----
    st.sidebar.header("➕ Agregar movimiento")
    tdate = st.sidebar.date_input("Fecha", value=date.today())
    kind  = st.sidebar.selectbox("Tipo", ["ingreso", "gasto", "inversion"])
    cats_df = load_categories(user_id)
    cats_opts = cats_df.loc[cats_df["kind"] == kind, "name"].tolist() or ["Sin categoría"]
    category = st.sidebar.selectbox("Categoría", cats_opts)
    amount = st.sidebar.number_input("Monto", min_value=0.0, step=100.0, format="%.2f")
    note = st.sidebar.text_area("Nota (opcional)", placeholder="detalle…", height=80)

    if st.sidebar.button("Agregar", use_container_width=True):
        if amount > 0 and category:
            add_transaction(user_id, tdate, amount, kind, category, note)
            st.sidebar.success("Movimiento agregado.")
        else:
            st.sidebar.warning("Completá monto y categoría.")

    # ---- Sidebar: administrar categorías ----
    with st.sidebar.expander("⚙️ Administrar categorías", expanded=False):
        new_name = st.text_input("Nueva categoría")
        new_kind = st.selectbox("Tipo de la categoría", ["ingreso", "gasto", "inversion"], key="new_kind")
        if st.button("Guardar categoría"):
            if new_name.strip():
                add_category(user_id, new_name, new_kind)
                st.success("Categoría guardada.")
            else:
                st.warning("Poné un nombre.")

        st.markdown("---")
        st.subheader("Eliminar categoría")
        all_cats = load_categories(user_id)["name"].tolist()
        sel_del = st.selectbox("Categoría a borrar", all_cats) if all_cats else None

        tx_df = load_transactions(user_id)
        count_assoc = int((tx_df["category"] == sel_del).sum()) if (sel_del and not tx_df.empty) else 0
        st.caption(f"Movimientos asociados: **{count_assoc}**")

        reassign = None
        if count_assoc > 0:
            if st.checkbox("Reasignar movimientos antes de borrar"):
                candidates = [c for c in all_cats if c != sel_del]
                reassign = st.selectbox("Reasignar a", candidates) if candidates else None

        if st.button("Borrar categoría"):
            if sel_del:
                delete_category(user_id, sel_del, reassign)
                st.success("Categoría eliminada.")
            else:
                st.warning("No hay categoría para borrar.")

    # ---- Contenido principal ----
    st.title("💸 Finanzas personales")

    # Cargar datos del usuario
    df = load_transactions(user_id)

    # Filtros de período
    st.subheader("Filtros")
    colp1, colp2, colp3, colp4 = st.columns(4)
    today = date.today()
    if "rng" not in st.session_state:
        st.session_state.rng = (today.replace(day=1), today)

    if colp1.button("Hoy"):       st.session_state.rng = (today, today)
    if colp2.button("Este mes"):  st.session_state.rng = (today.replace(day=1), today)
    if colp3.button("Este año"):  st.session_state.rng = (date(today.year, 1, 1), today)

    with st.container():
        r1, r2 = st.columns(2)
        start = r1.date_input("Desde", value=st.session_state.rng[0], key="d_from")
        end   = r2.date_input("Hasta", value=st.session_state.rng[1], key="d_to")
        if start > end:
            st.warning("La fecha 'Desde' no puede ser mayor a 'Hasta'.")
        st.session_state.rng = (start, end)

    # Filtrado por rango
    if not df.empty:
        fdf = df[(df["tdate"] >= pd.to_datetime(start)) & (df["tdate"] <= pd.to_datetime(end))].copy()
    else:
        fdf = df.copy()

    # KPIs y banner
    if fdf.empty:
        ing = gas = inv = balance = 0.0
    else:
        ing = fdf.loc[fdf["kind"]=="ingreso","amount"].sum()
        gas = fdf.loc[fdf["kind"]=="gasto","amount"].sum()
        inv = fdf.loc[fdf["kind"]=="inversion","amount"].sum()
        balance = ing - gas - inv

    positivo = ing >= (gas + inv)
    color = "#16a34a" if positivo else "#dc2626"
    emoji = "✅" if positivo else "⚠️"
    st.markdown(f"""
    <div style="padding:14px 18px;border-radius:12px;background:{color};color:white;display:flex;justify-content:space-between;align-items:center;">
      <div style="font-size:20px;font-weight:700;">{emoji} Balance del período: ${balance:,.2f}</div>
      <div style="opacity:.95;">Ingresos: ${ing:,.2f} · Gastos: ${gas:,.2f} · Inversiones: ${inv:,.2f}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### Balance histórico")
    if fdf.empty:
        st.info("Aún no hay datos para graficar.")
    else:
        s = (
            fdf.assign(delta=fdf.apply(lambda r: r["amount"] if r["kind"]=="ingreso" else -r["amount"], axis=1))
               .groupby("tdate")["delta"].sum()
               .cumsum()
               .reset_index()
               .rename(columns={"delta":"balance"})
        )
        fig = px.line(s, x="tdate", y="balance", markers=True)
        fig.update_layout(height=360, margin=dict(l=10,r=10,t=20,b=10))
        st.plotly_chart(fig, use_container_width=True)

# ------------------ MAIN ------------------
def main():
    user = current_user()
    if not user:
        auth_view()
        return
    app_view(user)

if __name__ == "__main__":
    main()
