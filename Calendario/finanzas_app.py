# finanzas_app.py
# --------------------------------------------------------------------------------------
# Finanzas personales (Streamlit + Supabase) - versión con login robusto v1/v2
# --------------------------------------------------------------------------------------

from datetime import date
from dateutil.relativedelta import relativedelta
import pandas as pd
import plotly.express as px
import streamlit as st

# --- Supabase SDK (v2 recomendado) ---
# El import es el mismo en v1/v2
from supabase import create_client, Client

# --------------------------------------------------------------------------------------
# Config general
# --------------------------------------------------------------------------------------
st.set_page_config(page_title="Finanzas Inteligentes", page_icon="💸", layout="wide")

# --------------------------------------------------------------------------------------
# Helpers de Supabase
# --------------------------------------------------------------------------------------
from supabase import create_client, Client
import streamlit as st

def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_ANON_KEY"]

    # SIEMPRE crear un cliente fresco por sesión/petición
    supa = create_client(url, key)

    # Si tenés una sesión guardada en session_state, rehidratarla
    sess = st.session_state.get("sb_session")
    if sess:
        try:
            supa.auth.set_session(sess["access_token"], sess["refresh_token"])
        except Exception:
            # Si falló, limpiar la sesión corrupta
            for k in ("sb_session", "user"):
                st.session_state.pop(k, None)
    return supa


def do_rerun():
    # Compat: st.rerun() (>=1.27) o st.experimental_rerun (anteriores)
    getattr(st, "rerun", getattr(st, "experimental_rerun", lambda: None))()

def supa_get_user(supa: Client):
    """Obtiene el usuario actual para v2 o v1."""
    # v2: supa.auth.get_user()
    try:
        res = supa.auth.get_user()
        return getattr(res, "user", None)
    except Exception:
        pass
    # v1: intentar via session/current_user no estándar
    try:
        # No hay API directa v1; devolvemos None y delegamos a sesión local
        return None
    except Exception:
        return None

def supa_sign_in(supa: Client, email: str, password: str):
    """
    Login que intenta API v2; si falla, prueba v1.
    Devuelve (user, session, error_message)
    """
    try:
        # v2
        res = supa.auth.sign_in_with_password({"email": email, "password": password})
        return getattr(res, "user", None), getattr(res, "session", None), None
    except AttributeError:
        # probablemente sea v1
        try:
            res = supa.auth.sign_in(email=email, password=password)  # v1
            # v1 puede devolver dict o un objeto
            user = getattr(res, "user", None) if not isinstance(res, dict) else res.get("user")
            sess = getattr(res, "session", None) if not isinstance(res, dict) else res.get("session")
            return user, sess, None
        except Exception as e:
            return None, None, str(e)
    except Exception as e:
        return None, None, getattr(e, "message", str(e))

def supa_sign_up(supa: Client, email: str, password: str):
    """Registro robusto (v2 / v1)."""
    try:
        # v2
        res = supa.auth.sign_up({"email": email, "password": password})
        return getattr(res, "user", None), None
    except AttributeError:
        # v1
        try:
            res = supa.auth.sign_up(email=email, password=password)
            user = getattr(res, "user", None) if not isinstance(res, dict) else res.get("user")
            return user, None
        except Exception as e:
            return None, str(e)
    except Exception as e:
        return None, getattr(e, "message", str(e))

def supa_sign_out(supa: Client):
    try:
        supa.auth.sign_out()
    except Exception:
        pass
    # limpiar todo lo de Streamlit
    st.session_state.clear()
    do_rerun()

# --------------------------------------------------------------------------------------
# Datos / consultas
# --------------------------------------------------------------------------------------
def ensure_defaults(supa: Client, user_id: str):
    """Crea categorías por defecto para el usuario si no existen."""
    defaults = [
        ("Sueldo", "ingreso"),
        ("Ventas", "ingreso"),
        ("Alquiler", "gasto"),
        ("Comida", "gasto"),
        ("Servicios", "gasto"),
        ("Inversiones", "inversion"),
    ]
    for name, kind in defaults:
        try:
            existing = (
                supa.table("categories")
                .select("id")
                .eq("user_id", user_id)
                .eq("name", name)
                .limit(1)
                .execute()
            )
            rows = existing.data if hasattr(existing, "data") else existing.get("data", [])
            if not rows:
                supa.table("categories").insert(
                    {"user_id": user_id, "name": name, "kind": kind}
                ).execute()
        except Exception:
            pass

@st.cache_data(ttl=60)
def load_df_cached(user_id: str):
    """Carga DF de movimientos + categorías (cache POR USUARIO)."""
    supa = get_supabase()
    # Transacciones
    res_tx = (
        supa.table("transactions")
        .select("*")
        .eq("user_id", user_id)
        .order("tdate")
        .execute()
    )
    tx = res_tx.data if hasattr(res_tx, "data") else res_tx.get("data", [])
    df = pd.DataFrame(tx or [], columns=["id","user_id","tdate","amount","kind","category","note"])
    if not df.empty:
        df["tdate"] = pd.to_datetime(df["tdate"])
        df["amount"] = pd.to_numeric(df["amount"])
    else:
        df = pd.DataFrame(columns=["id","user_id","tdate","amount","kind","category","note"])
    # Categorías
    res_cat = supa.table("categories").select("id,name,kind").eq("user_id", user_id).execute()
    cats = res_cat.data if hasattr(res_cat, "data") else res_cat.get("data", [])
    df_cats = pd.DataFrame(cats or [], columns=["id","name","kind"])
    return df, df_cats

def add_category(supa: Client, user_id: str, name: str, kind: str):
    supa.table("categories").insert({"user_id": user_id, "name": name, "kind": kind}).execute()
    st.cache_data.clear()

def delete_category(supa: Client, user_id: str, category_name: str):
    # Ojo: Si hay transacciones con esa categoría, bloquear o reasignar en tu flujo
    supa.table("categories").delete().eq("user_id", user_id).eq("name", category_name).execute()
    st.cache_data.clear()

def add_transaction(supa: Client, user_id: str, tdate_, amount_, kind_, category_, note_):
    supa.table("transactions").insert({
        "user_id": user_id,
        "tdate": str(tdate_),
        "amount": float(amount_),
        "kind": kind_,
        "category": category_,
        "note": note_,
    }).execute()
    st.cache_data.clear()

# --------------------------------------------------------------------------------------
# UI: Login/Registro
# --------------------------------------------------------------------------------------
def ui_auth():
    supa = get_supabase()

    st.title("💸 Finanzas — Iniciar sesión")

    tabs = st.tabs(["🔐 Ingresar", "🆕 Registrarme"])
    with tabs[0]:
        email = st.text_input("Email", key="login_email")
        pwd   = st.text_input("Contraseña", type="password", key="login_pwd")
        if st.button("Ingresar", use_container_width=True):
            if not email or not pwd:
                st.warning("Completá email y contraseña.")
            else:
                with st.spinner("Ingresando..."):
                    user, session, err = supa_sign_in(supa, email.strip(), pwd)
                    if err:
                        st.error(f"No pudimos iniciar sesión: {err}")
                    elif not user:
                        st.error("Login falló. Revisá email/contraseña.")
                    else:
                        # Si tu proyecto requiere email confirmado:
                        if getattr(user, "email_confirmed_at", None) is None:
                            st.warning("Debés confirmar tu email antes de ingresar.")
                        st.session_state["user"] = user
                        st.session_state["sb_session"] = session
                        do_rerun()

    with tabs[1]:
        rmail = st.text_input("Email", key="reg_email")
        rpwd1 = st.text_input("Contraseña", type="password", key="reg_pwd1")
        rpwd2 = st.text_input("Repetir contraseña", type="password", key="reg_pwd2")
        if st.button("Crear cuenta", use_container_width=True):
            if not rmail or not rpwd1:
                st.warning("Completá email y contraseña.")
            elif rpwd1 != rpwd2:
                st.warning("Las contraseñas no coinciden.")
            else:
                with st.spinner("Creando cuenta..."):
                    user, err = supa_sign_up(supa, rmail.strip(), rpwd1)
                    if err:
                        st.error(f"No pudimos crear la cuenta: {err}")
                    else:
                        st.success("Cuenta creada. Revisá tu email para confirmar la cuenta.")
                        st.info("Luego, volvé a esta pantalla y logueate.")

# --------------------------------------------------------------------------------------
# UI: App principal
# --------------------------------------------------------------------------------------
def ui_app(user):
    supa = get_supabase()
    # Asegurar categorías por defecto
    user_id = user.id if hasattr(user, "id") else user.get("id")
    ensure_defaults(supa, user_id)

    # Sidebar: usuario y logout
    with st.sidebar:
        st.success(f"Sesión: {getattr(user, 'email', '') or user.get('email','')}")
        if st.button("Cerrar sesión", use_container_width=True):
            supa_sign_out(supa)

        st.divider()
        st.header("➕ Agregar movimiento")

        # Cargar categorías/df para selects
        df, df_cats = load_df_cached(user_id)

        tdate_ = st.date_input("Fecha", value=date.today())
        kind_  = st.selectbox("Tipo", ["ingreso", "gasto", "inversion"])
        # categorías filtradas por tipo
        cats_for_kind = df_cats[df_cats["kind"] == kind_]["name"].sort_values().tolist()
        category_ = st.selectbox("Categoría", cats_for_kind or ["(sin categorías)"])
        amount_   = st.number_input("Monto", min_value=0.0, step=100.0, format="%.2f")
        note_     = st.text_input("Nota (opcional)")

        if st.button("Agregar", use_container_width=True):
            if not cats_for_kind:
                st.warning("No hay categorías para ese tipo.")
            elif amount_ <= 0:
                st.warning("El monto debe ser > 0.")
            else:
                add_transaction(supa, user_id, tdate_, amount_, kind_, category_, note_)
                st.success("Movimiento guardado.")

        st.divider()
        with st.expander("⚙️ Administrar categorías"):
            new_name = st.text_input("Nueva categoría")
            new_kind = st.selectbox("Tipo", ["ingreso","gasto","inversion"], key="new_kind")
            if st.button("Guardar categoría", use_container_width=True):
                if new_name.strip():
                    add_category(supa, user_id, new_name.strip(), new_kind)
                    st.success("Categoría guardada.")
                else:
                    st.warning("Poné un nombre.")

            st.write("---")
            # borrar categoría
            df, df_cats = load_df_cached(user_id)
            cat_to_del = st.selectbox("Categoría a borrar", df_cats["name"].sort_values().tolist() or ["(no hay)"])
            if st.button("Borrar categoría", type="secondary", use_container_width=True):
                if cat_to_del and cat_to_del != "(no hay)":
                    delete_category(supa, user_id, cat_to_del)
                    st.success("Categoría borrada.")

    # --------- Main: filtros + KPIs + gráfico simple ----------
    st.title("Finanzas personales")

    # Período rápido
    colp1, colp2, colp3, colp4 = st.columns(4)
    today = date.today()
    with colp1:
        if st.button("Hoy"):
            st.session_state["period_start"] = today
            st.session_state["period_end"] = today
            do_rerun()
    with colp2:
        if st.button("Este mes"):
            st.session_state["period_start"] = today.replace(day=1)
            st.session_state["period_end"] = today
            do_rerun()
    with colp3:
        if st.button("Este año"):
            st.session_state["period_start"] = today.replace(month=1, day=1)
            st.session_state["period_end"] = today
            do_rerun()
    with colp4:
        st.write(" ")

    # Rango manual
    ps = st.session_state.get("period_start", today.replace(day=1))
    pe = st.session_state.get("period_end", today)
    c1, c2 = st.columns(2)
    with c1:
        start = st.date_input("Desde", value=ps)
    with c2:
        end   = st.date_input("Hasta", value=pe)

    # DF filtrado
    df, df_cats = load_df_cached(user_id)
    if not df.empty:
        fdf = df[(df["tdate"] >= pd.to_datetime(start)) & (df["tdate"] <= pd.to_datetime(end))].copy()
    else:
        fdf = df.copy()

    # KPIs
    ing = fdf.loc[fdf["kind"]=="ingreso","amount"].sum()
    gas = fdf.loc[fdf["kind"]=="gasto","amount"].sum()
    inv = fdf.loc[fdf["kind"]=="inversion","amount"].sum()
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
        unsafe_allow_html=True,
    )

    st.write("")
    # Gráfico simple: saldo acumulado por día
    if fdf.empty:
        st.info("Aún no hay datos para graficar.")
    else:
        # saldo acumulado
        tmp = fdf.sort_values("tdate").copy()
        tmp["delta"] = tmp.apply(lambda r: r["amount"] if r["kind"]=="ingreso" else (-r["amount"]), axis=1)
        # "inversion" la consideramos salida de dinero
        tmp.loc[tmp["kind"]=="inversion", "delta"] = -tmp["amount"]
        g = tmp.groupby("tdate", as_index=False)["delta"].sum()
        g["saldo"] = g["delta"].cumsum()

        fig = px.line(g, x="tdate", y="saldo", markers=True, title="Balance histórico")
        fig.update_layout(margin=dict(l=0,r=0,t=40,b=0))
        st.plotly_chart(fig, use_container_width=True)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main():
    supa = get_supabase()

    # Intentar recuperar usuario si quedó en sesión (v2 setea cookies)
    user = st.session_state.get("user")
    if user is None:
        # Intentar por SDK (solo v2 devuelve info)
        try:
            user = supa_get_user(supa)
        except Exception:
            user = None
        if user:
            st.session_state["user"] = user

    if st.session_state.get("user") is None:
        ui_auth()
    else:
        ui_app(st.session_state["user"])

if __name__ == "__main__":
    main()
