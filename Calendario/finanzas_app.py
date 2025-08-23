# finanzas_app.py
# --------------------------------------------------------------------------------------
# Finanzas personales (Streamlit + Supabase)
# - Cliente Supabase por request + rehidratación de sesión (SIN cache global)
# - Login / Registro robustos (SDK v2)
# - Sesiones aisladas por navegador (st.session_state)
# - Caché de datos por usuario (user_id)
# - KPIs + gráfico + admin de categorías y movimientos
# - RESUMEN POR CATEGORÍA (nuevo)
# --------------------------------------------------------------------------------------

from datetime import date
from dateutil.relativedelta import relativedelta
import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client, Client

# ---------------------------------- Config ----------------------------------
st.set_page_config(page_title="Finanzas Inteligentes", page_icon="💸", layout="wide")

# Limpiar tokens en la URL (si alguien abre la app desde un link de verificación)
try:
    if any(k in st.query_params for k in ("access_token", "refresh_token")):
        st.query_params.clear()
except Exception:
    pass

def do_rerun():
    getattr(st, "rerun", getattr(st, "experimental_rerun", lambda: None))()

# ------------------------- Supabase client (sin cache) -----------------------
def get_supabase() -> Client:
    """Crea SIEMPRE un cliente nuevo y lo rehidrata con la sesión guardada en session_state."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_ANON_KEY"]
    supa = create_client(url, key)

    sess = st.session_state.get("sb_session")
    if isinstance(sess, dict) and sess.get("access_token") and sess.get("refresh_token"):
        try:
            supa.auth.set_session(sess["access_token"], sess["refresh_token"])
        except Exception:
            # si falla, limpiamos sesión corrupta
            for k in ("sb_session", "user"):
                st.session_state.pop(k, None)
    return supa

# ------------------------------ Auth helpers --------------------------------
def sign_in(email: str, password: str):
    supa = get_supabase()
    res = supa.auth.sign_in_with_password({"email": email, "password": password})
    # Guardar SOLO en el session_state del navegador actual
    st.session_state["user"] = {"id": res.user.id, "email": res.user.email}
    st.session_state["sb_session"] = {
        "access_token": res.session.access_token,
        "refresh_token": res.session.refresh_token,
    }
    st.cache_data.clear()
    do_rerun()

def sign_up(email: str, password: str):
    supa = get_supabase()
    supa.auth.sign_up({"email": email, "password": password})

def sign_out():
    try:
        get_supabase().auth.sign_out()
    except Exception:
        pass
    # Limpiar absolutamente todo lo sensible
    for k in ("user", "sb_session"):
        st.session_state.pop(k, None)
    st.cache_data.clear()
    do_rerun()

def current_user():
    """Devuelve el usuario actual leyendo session_state o preguntando al SDK."""
    # Si ya tenemos usuario en session_state, usarlo
    if isinstance(st.session_state.get("user"), dict) and st.session_state["user"].get("id"):
        return st.session_state["user"]

    # Si no, intentamos preguntarle al SDK (cookies v2)
    try:
        res = get_supabase().auth.get_user()
        if res and res.user:
            st.session_state["user"] = {"id": res.user.id, "email": res.user.email}
            return st.session_state["user"]
    except Exception:
        pass
    return None

# ----------------------------- Data layer (RLS) ------------------------------
@st.cache_data(ttl=60)
def load_transactions(user_id: str) -> pd.DataFrame:
    supa = get_supabase()
    res = supa.table("transactions").select("*").eq("user_id", user_id).order("tdate").execute()
    data = res.data or []
    df = pd.DataFrame(data, columns=["id","user_id","tdate","amount","kind","category","note"])
    if not df.empty:
        df["tdate"] = pd.to_datetime(df["tdate"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    return df

@st.cache_data(ttl=60)
def load_categories(user_id: str) -> pd.DataFrame:
    supa = get_supabase()
    res = supa.table("categories").select("id,name,kind").eq("user_id", user_id).order("name").execute()
    data = res.data or []
    return pd.DataFrame(data, columns=["id","name","kind"])

def ensure_default_categories(user_id: str):
    supa = get_supabase()
    cat_df = load_categories(user_id)
    if not cat_df.empty:
        return
    defaults = [
        ("Sueldo","ingreso"), ("Extra","ingreso"),
        ("Alquiler","gasto"), ("Comida","gasto"), ("Transporte","gasto"),
        ("Servicios","gasto"), ("Entretenimiento","gasto"),
        ("Salud","gasto"), ("Educación","gasto"),
        ("Ahorro/ETF","inversion"), ("Cripto","inversion"), ("Plazo Fijo","inversion"),
    ]
    for name, kind in defaults:
        try:
            supa.table("categories").insert({"user_id": user_id, "name": name, "kind": kind}).execute()
        except Exception:
            pass
    st.cache_data.clear()

def add_category(user_id: str, name: str, kind: str):
    get_supabase().table("categories").insert({"user_id": user_id, "name": name.strip(), "kind": kind}).execute()
    st.cache_data.clear()

def delete_category(user_id: str, name: str, reassign_to: str | None):
    supa = get_supabase()
    if reassign_to:
        supa.table("transactions").update({"category": reassign_to}).eq("user_id", user_id).eq("category", name).execute()
    supa.table("categories").delete().eq("user_id", user_id).eq("name", name).execute()
    st.cache_data.clear()

def add_transaction(user_id: str, tdate: date, amount: float, kind: str, category: str, note: str):
    get_supabase().table("transactions").insert({
        "user_id": user_id,
        "tdate": tdate.isoformat(),
        "amount": float(amount),
        "kind": kind,
        "category": category,
        "note": note or "",
    }).execute()
    st.cache_data.clear()

def delete_transaction(user_id: str, tx_id: int):
    get_supabase().table("transactions").delete().eq("user_id", user_id).eq("id", tx_id).execute()
    st.cache_data.clear()

# ------------------------------- UI: Auth -----------------------------------
def ui_auth():
    st.title("💸 Finanzas — Acceso")
    tabs = st.tabs(["🔐 Ingresar", "🆕 Registrarme"])

    # -------- Tab: Ingresar --------
    with tabs[0]:
        email = st.text_input("Email", key="login_email")
        pwd   = st.text_input("Contraseña", type="password", key="login_pwd")
        if st.button("Ingresar", use_container_width=True, key="login_btn"):
            if not email or not pwd:
                st.warning("Completá email y contraseña.")
            else:
                with st.spinner("Ingresando..."):
                    try:
                        sign_in(email.strip(), pwd)
                    except Exception as e:
                        st.error(f"No pudimos iniciar sesión: {e}")

    # -------- Tab: Registrarme --------
    with tabs[1]:
        rmail = st.text_input("Email (registro)", key="reg_email")
        rpwd1 = st.text_input("Contraseña", type="password", key="reg_pwd1")
        rpwd2 = st.text_input("Repetir contraseña", type="password", key="reg_pwd2")
        if st.button("Crear cuenta", use_container_width=True, key="reg_btn"):
            if not rmail or not rpwd1:
                st.warning("Completá email y contraseña.")
            elif rpwd1 != rpwd2:
                st.warning("Las contraseñas no coinciden.")
            else:
                with st.spinner("Creando cuenta..."):
                    try:
                        sign_up(rmail.strip(), rpwd1)
                        st.success("Cuenta creada. Revisá tu email para confirmar y luego ingresá.")
                    except Exception as e:
                        st.error(f"No pudimos registrar: {e}")


# ----------------------------- UI: App principal -----------------------------
def ui_app(user: dict):
    user_id = user["id"]
    ensure_default_categories(user_id)

    # --- Sidebar: sesión + logout ---
    st.sidebar.success(f"Sesión: {user.get('email','')}")
    if st.sidebar.button("Cerrar sesión", use_container_width=True):
        sign_out()

    # --- Sidebar: alta de movimiento ---
    st.sidebar.header("➕ Agregar movimiento")
    tdate = st.sidebar.date_input("Fecha", value=date.today())
    kind  = st.sidebar.selectbox("Tipo", ["ingreso","gasto","inversion"])
    cats_df = load_categories(user_id)
    cats_opts = cats_df.loc[cats_df["kind"] == kind, "name"].tolist() or ["(sin categorías)"]
    category = st.sidebar.selectbox("Categoría", cats_opts)
    amount = st.sidebar.number_input("Monto", min_value=0.0, step=100.0, format="%.2f")
    note = st.sidebar.text_input("Nota (opcional)")
    if st.sidebar.button("Agregar", use_container_width=True):
        if category == "(sin categorías)":
            st.sidebar.warning("Primero creá una categoría para ese tipo.")
        elif amount <= 0:
            st.sidebar.warning("El monto debe ser > 0.")
        else:
            try:
                add_transaction(user_id, tdate, amount, kind, category, note)
                st.sidebar.success("Movimiento agregado.")
            except Exception as e:
                st.sidebar.error(f"No se pudo guardar: {e}")

    # --- Sidebar: administrar categorías ---
    with st.sidebar.expander("⚙️ Administrar categorías", expanded=False):
        new_name = st.text_input("Nueva categoría")
        new_kind = st.selectbox("Tipo", ["ingreso","gasto","inversion"], key="new_kind")
        if st.button("Guardar categoría"):
            if new_name.strip():
                add_category(user_id, new_name.strip(), new_kind)
                st.success("Categoría guardada.")
            else:
                st.warning("Poné un nombre.")

        st.divider()
        st.subheader("Eliminar categoría")
        all_cats = load_categories(user_id)["name"].tolist()
        cat_del = st.selectbox("Categoría a borrar", all_cats) if all_cats else None

        # Contar movimientos asociados
        tx_df = load_transactions(user_id)
        count_assoc = int((tx_df["category"] == cat_del).sum()) if (cat_del and not tx_df.empty) else 0
        st.caption(f"Movimientos asociados: **{count_assoc}**")

        reassign_to = None
        if count_assoc > 0:
            if st.checkbox("Reasignar movimientos antes de borrar"):
                candidates = [c for c in all_cats if c != cat_del]
                reassign_to = st.selectbox("Reasignar a", candidates) if candidates else None

        if st.button("Borrar categoría", type="secondary"):
            if cat_del:
                if count_assoc > 0 and not reassign_to:
                    st.error("Hay movimientos con esa categoría. Elegí una categoría destino para reasignar.")
                else:
                    delete_category(user_id, cat_del, reassign_to)
                    st.success("Categoría eliminada.")
            else:
                st.warning("No hay categoría para borrar.")

    # ------------------- Main content -------------------
    st.title("💸 Finanzas personales")

    # Periodo rápido
    colp1, colp2, colp3, colp4 = st.columns(4)
    today = date.today()
    if "period_start" not in st.session_state:
        st.session_state["period_start"] = today.replace(day=1)
    if "period_end" not in st.session_state:
        st.session_state["period_end"] = today

    if colp1.button("Hoy"):
        st.session_state["period_start"] = today
        st.session_state["period_end"] = today
        do_rerun()
    if colp2.button("Este mes"):
        st.session_state["period_start"] = today.replace(day=1)
        st.session_state["period_end"] = today
        do_rerun()
    if colp3.button("Este año"):
        st.session_state["period_start"] = date(today.year,1,1)
        st.session_state["period_end"] = today
        do_rerun()
    colp4.write(" ")

    # Rango manual
    c1, c2 = st.columns(2)
    start = c1.date_input("Desde", value=st.session_state["period_start"])
    end   = c2.date_input("Hasta", value=st.session_state["period_end"])
    if (start, end) != (st.session_state["period_start"], st.session_state["period_end"]):
        st.session_state["period_start"], st.session_state["period_end"] = start, end

    # Datos
    df = load_transactions(user_id)
    if not df.empty:
        fdf = df[(df["tdate"] >= pd.to_datetime(start)) & (df["tdate"] <= pd.to_datetime(end))].copy()
    else:
        fdf = df.copy()

    # KPIs
    ing = fdf.loc[fdf["kind"]=="ingreso","amount"].sum() if not fdf.empty else 0.0
    gas = fdf.loc[fdf["kind"]=="gasto","amount"].sum() if not fdf.empty else 0.0
    inv = fdf.loc[fdf["kind"]=="inversion","amount"].sum() if not fdf.empty else 0.0
    balance = ing - gas - inv
    positivo = balance >= 0
    color = "#16a34a" if positivo else "#dc2626"
    emoji = "✅" if positivo else "⚠️"
    st.markdown(
        f"""
        <div style="padding:14px 18px;border-radius:12px;background:{color};color:white;display:flex;justify-content:space-between;align-items:center;">
          <div style="font-size:20px;font-weight:700;">{emoji} Balance del período: ${balance:,.2f}</div>
          <div style="opacity:.95;">Ingresos: ${ing:,.2f} · Gastos: ${gas:,.2f} · Inversiones: ${inv:,.2f}</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.write("")
    # Gráfico simple: saldo acumulado por día dentro del rango
    if fdf.empty:
        st.info("Aún no hay datos para graficar.")
    else:
        tmp = fdf.sort_values("tdate").copy()
        tmp["delta"] = tmp.apply(lambda r: r["amount"] if r["kind"]=="ingreso" else -r["amount"], axis=1)
        tmp.loc[tmp["kind"]=="inversion", "delta"] = -tmp["amount"]  # inversión como salida
        g = tmp.groupby("tdate", as_index=False)["delta"].sum()
        g["saldo"] = g["delta"].cumsum()
        fig = px.line(g, x="tdate", y="saldo", markers=True, title="Balance histórico")
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=40, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # --------- Resumen por categoría (suma y % del total) ----------
    st.markdown("### 📊 Resumen por categoría")

    if fdf.empty:
        st.info("Aún no hay datos para resumir en este período.")
    else:
        # Elegir qué tipo mostrar
        tipo = st.radio(
            "Tipo de movimientos a resumir",
            ["gasto", "ingreso", "inversion", "salidas (gasto + inversión)"],
            horizontal=True,
            index=0,
            key="tipo_resumen"
        )

        if "salidas" in tipo:
            sub = fdf[fdf["kind"].isin(["gasto", "inversion"])].copy()
            titulo = "Salidas (gasto + inversión)"
        else:
            sub = fdf[fdf["kind"] == tipo].copy()
            titulo = tipo.capitalize()

        if sub.empty:
            st.info(f"No hay movimientos de **{titulo}** en el rango seleccionado.")
        else:
            # Agrupar por categoría
            resumen = (
                sub.groupby("category", as_index=False)
                   .agg(total=("amount", "sum"), operaciones=("id", "count"))
                   .sort_values("total", ascending=False)
            )
            total_general = resumen["total"].sum()
            resumen["% del total"] = (resumen["total"] / total_general * 100).round(2)

            # Tabla
            st.dataframe(
                resumen,
                use_container_width=True,
                hide_index=True
            )

            # Gráfico
            fig_cat = px.bar(
                resumen,
                x="category", y="total",
                title=f"Total por categoría — {titulo}",
                text_auto=True
            )
            fig_cat.update_layout(height=360, margin=dict(l=10, r=10, t=40, b=0))
            st.plotly_chart(fig_cat, use_container_width=True)

            # Exportar CSV
            st.download_button(
                "⬇️ Exportar resumen (CSV)",
                data=resumen.to_csv(index=False).encode("utf-8"),
                file_name=f"resumen_categorias_{tipo.replace(' ','_')}.csv",
                mime="text/csv",
                use_container_width=True
            )

    # Tabla + borrado
    st.markdown("### Movimientos del período")
    if not fdf.empty:
        st.dataframe(
            fdf[["id","tdate","kind","category","amount","note"]].sort_values("tdate", ascending=False),
            use_container_width=True,
            hide_index=True
        )
        with st.expander("🗑️ Borrar movimiento"):
            tx_id = st.number_input("ID a borrar", min_value=0, step=1)
            if st.button("Borrar ID"):
                try:
                    delete_transaction(user_id, int(tx_id))
                    st.success("Movimiento eliminado.")
                except Exception as e:
                    st.error(f"No se pudo borrar: {e}")
    else:
        st.write("—")

# ------------------------------------ Main -----------------------------------
def main():
    user = current_user()
    if not user:
        ui_auth()
    else:
        ui_app(user)

if __name__ == "__main__":
    main()
