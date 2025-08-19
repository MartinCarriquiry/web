import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

# ---------- Supabase ----------
from supabase import create_client, Client
import streamlit as st

@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY")
    if not url or not key:
        st.error(
            "Faltan secrets. En Streamlit Cloud (o en .streamlit/secrets.toml) "
            "agregá:\n\n"
            'SUPABASE_URL="https://<tu-proyecto>.supabase.co"\n'
            'SUPABASE_ANON_KEY="ey..."'
        )
        st.stop()
    return create_client(url, key)

supa = get_supabase()


# ---------- Auth (login/registro) ----------
def auth_ui():
    st.title("💸 Finanzas — Iniciar sesión")

    tab1, tab2 = st.tabs(["🔐 Ingresar", "🆕 Registrarme"])

    with tab1:
        email = st.text_input("Email")
        pwd = st.text_input("Contraseña", type="password")
        if st.button("Ingresar", type="primary", use_container_width=True):
            try:
                res = supa.auth.sign_in_with_password({"email": email, "password": pwd})
                st.session_state["user"] = res.user
                st.success("Bienvenido 👋")
                st.rerun()
            except Exception as e:
                st.error("Email o contraseña inválidos.")

    with tab2:
        email_r = st.text_input("Email (registro)", key="r1")
        pwd_r = st.text_input("Contraseña (registro)", type="password", key="r2")
        if st.button("Crear cuenta", use_container_width=True):
            try:
                res = supa.auth.sign_up({"email": email_r, "password": pwd_r})
                st.success("Cuenta creada. Revisá tu email para terminar la verificación.")
            except Exception as e:
                st.error("No se pudo crear la cuenta.")

def require_user():
    # restaurar sesión si existe
    if "user" not in st.session_state:
        sess = supa.auth.get_session()
        if sess and sess.user:
            st.session_state["user"] = sess.user

    if "user" not in st.session_state:
        auth_ui()
        st.stop()

require_user()
user = st.session_state["user"]
user_id = user.id

# ---------- Helpers DB (Supabase) ----------
def ensure_defaults():
    defaults = [
        ("Sueldo","ingreso"),("Extra","ingreso"),
        ("Alquiler","gasto"),("Comida","gasto"),("Transporte","gasto"),
        ("Servicios","gasto"),("Entretenimiento","gasto"),
        ("Salud","gasto"),("Educación","gasto"),
        ("Ahorro/ETF","inversion"),("Cripto","inversion"),("Plazo Fijo","inversion"),
    ]
    existing = supa.table("categories").select("name").eq("user_id", user_id).execute()
    have = {r["name"] for r in (existing.data or [])}
    rows = [{"user_id": user_id, "name": n, "type": k} for n,k in defaults if n not in have]
    if rows:
        supa.table("categories").insert(rows).execute()

@st.cache_data(ttl=30)
def load_df():
    cols = ["id","user_id","tdate","amount","type","category","note"]
    res = supa.table("transactions").select("*")\
        .eq("user_id", user_id).order("tdate", desc=True).execute()

    df = pd.DataFrame(res.data or [], columns=cols)

    if df.empty:
        return df

    df["tdate"] = pd.to_datetime(df["tdate"], errors="coerce")
    # A veces Supabase devuelve Decimal/str; lo normalizamos a float
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)

    return df


def list_categories(type=None) -> pd.DataFrame:
    q = supa.table("categories").select("name,type").eq("user_id", user_id)
    if type: q = q.eq("type", type)
    res = q.order("name").execute()
    return pd.DataFrame(res.data or [])

def add_category(name, type):
    supa.table("categories").insert({"user_id": user_id, "name": name.strip(), "type": type}).execute()
    st.cache_data.clear()

def count_tx_by_category(cat_name) -> int:
    res = (supa.table("transactions")
           .select("id", count="exact")
           .eq("user_id", user_id).eq("category", cat_name).execute())
    return res.count or 0

def delete_category(cat_name, reassign_to=None):
    if reassign_to:
        supa.table("transactions").update({"category": reassign_to}) \
            .eq("user_id", user_id).eq("category", cat_name).execute()
    supa.table("categories").delete().eq("user_id", user_id).eq("name", cat_name).execute()
    st.cache_data.clear()

def add_transaction(tdate, amount, type, category, note):
    supa.table("transactions").insert({
        "user_id": user_id,
        "tdate": tdate.isoformat(),
        "amount": float(amount),
        "type": type,
        "category": category,
        "note": note
    }).execute()
    st.cache_data.clear()

def delete_transaction(row_id: int):
    supa.table("transactions").delete().eq("user_id", user_id).eq("id", row_id).execute()
    st.cache_data.clear()

# ---------- Serie de balance ----------
def balance_series(full_df: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    if full_df.empty:
        return pd.DataFrame(columns=["t", "neto", "acumulado"])
    tmp = full_df.copy()
    tmp["sign"] = tmp.apply(lambda r: r["amount"] if r["type"]=="ingreso" else -r["amount"], axis=1)
    if freq == "D":
        g = tmp.groupby(pd.Grouper(key="tdate", freq="D"))["sign"].sum().reset_index()
    elif freq == "M":
        g = tmp.groupby(pd.Grouper(key="tdate", freq="MS"))["sign"].sum().reset_index()
    else:
        g = tmp.groupby(pd.Grouper(key="tdate", freq="YS"))["sign"].sum().reset_index()
    g = g.rename(columns={"tdate":"t", "sign":"neto"})
    g["acumulado"] = g["neto"].cumsum()
    return g

def range_from_option(df_dates: pd.Series, opt: str) -> pd.Timestamp:
    if df_dates.empty:
        return pd.Timestamp.today().normalize()
    last = df_dates.max().normalize()
    if opt == "1D": return last - pd.Timedelta(days=1)
    if opt == "1S": return last - pd.Timedelta(weeks=1)
    if opt == "1M": return last - pd.DateOffset(months=1)
    if opt == "1A": return last - pd.DateOffset(years=1)
    return df_dates.min().normalize()  # MÁX

# ---------- UI ----------
st.set_page_config(page_title="Finanzas Inteligentes", page_icon="💸", layout="wide")
st.sidebar.success(f"Sesión: {user.email}")
if st.sidebar.button("Cerrar sesión"):
    supa.auth.sign_out()
    st.session_state.pop("user", None)
    st.rerun()

st.title("💸 Finanzas personales")
ensure_defaults()
df = load_df()

# Sidebar: alta + administrar categorías
st.sidebar.header("➕ Agregar movimiento")
c1, c2 = st.sidebar.columns(2)
tdate = c1.date_input("Fecha", value=date.today())
type = c2.selectbox("Tipo", ["ingreso","gasto","inversion"], index=1)
cats = list_categories(type)
category = st.sidebar.selectbox("Categoría", options=cats["name"].tolist() if not cats.empty else [])
amount = st.sidebar.number_input("Monto", min_value=0.0, step=100.0, format="%.2f")
note = st.sidebar.text_input("Nota (opcional)", placeholder="detalle…")
if st.sidebar.button("Agregar", type="primary", use_container_width=True):
    if amount > 0 and category:
        add_transaction(tdate, amount, type, category, note)
        st.success("Movimiento agregado.")

with st.sidebar.expander("⚙️ Administrar categorías"):
    new_name = st.text_input("Nueva categoría")
    new_type = st.selectbox("Tipo de la categoría", ["ingreso", "gasto", "inversion"], key="new_type")
    if st.button("Guardar categoría"):
        if new_name.strip():
            add_category(new_name, new_type)
            st.success("Categoría guardada.")
        else:
            st.warning("Poné un nombre.")
    st.divider()
    st.caption("Eliminar categoría")
    all_cats = list_categories()
    if not all_cats.empty:
        cat_del = st.selectbox("Categoría a borrar", all_cats["name"].tolist())
        tx_count = count_tx_by_category(cat_del) if cat_del else 0
        st.write(f"Movimientos asociados: **{tx_count}**")
        reasignar = st.checkbox("Reasignar movimientos antes de borrar", value=(tx_count > 0))
        re_to = None
        if reasignar:
            otras = [c for c in all_cats["name"].tolist() if c != cat_del]
            re_to = st.selectbox("Reasignar a", otras if otras else ["(no hay otras categorías)"])
            if re_to == "(no hay otras categorías)": re_to=None
        if st.button("Borrar categoría", type="secondary"):
            if tx_count > 0 and not re_to:
                st.error("Hay movimientos con esa categoría. Reasigná primero o creá otra categoría.")
            else:
                delete_category(cat_del, re_to)
                st.success("Categoría eliminada.")

# Filtros período
st.subheader("Filtros")
preset = st.segmented_control("Periodo", options=["Hoy","Este mes","Este año","Rango"], default="Este mes")
today = date.today()
if preset == "Hoy":
    start, end = today, today
elif preset == "Este mes":
    start = today.replace(day=1); end = (start + relativedelta(months=1) - timedelta(days=1))
elif preset == "Este año":
    start = date(today.year,1,1); end = date(today.year,12,31)
else:
    a,b = st.columns(2)
    start = a.date_input("Desde", value=today.replace(day=1))
    end   = b.date_input("Hasta", value=today)

if not df.empty:
    fdf = df[(df["tdate"] >= pd.to_datetime(start)) & (df["tdate"] <= pd.to_datetime(end))].copy()
else:
    fdf = df.copy()

# KPIs y banner
if fdf.empty:
    ing = gas = inv = 0.0
    balance = 0.0
else:
    ing = fdf.loc[fdf["type"]=="ingreso","amount"].sum()
    gas = fdf.loc[fdf["type"]=="gasto","amount"].sum()
    inv = fdf.loc[fdf["type"]=="inversion","amount"].sum()
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
st.divider()

# Gráfico balance (línea)
st.markdown("### Balance (línea)")
serie = balance_series(df, "D")
if serie.empty:
    st.info("Aún no hay datos para graficar.")
else:
    c1, c2 = st.columns([1,1])
    vista = c1.segmented_control("Rango", options=["1D","1S","1M","1A","MÁX"], default="MÁX")
    modo  = c2.segmented_control("Mostrar", options=["Neto","Acumulado"], default="Neto")
    start_window = range_from_option(serie["t"], vista)
    visor = serie[serie["t"] >= start_window].copy()
    ycol = "neto" if modo=="Neto" else "acumulado"
    fig = px.line(visor, x="t", y=ycol, markers=False, title=f"Balance {modo.lower()} ({vista})")
    fig.update_layout(height=340, yaxis_title="$", xaxis_title="")
    fig.add_hline(y=0, line_dash="dot", line_color="#888", opacity=0.4)
    st.plotly_chart(fig, use_container_width=True)

# Tabla de movimientos + borrado
st.markdown("### Movimientos del período")
if not fdf.empty:
    show = ["id","tdate","type","category","amount","note"]
    st.dataframe(fdf[show].sort_values("tdate", ascending=False), use_container_width=True, hide_index=True)
    with st.expander("🗑️ Borrar movimiento"):
        try_id = st.number_input("ID a borrar", min_value=0, step=1)
        if st.button("Borrar ID"):
            delete_transaction(int(try_id))
            st.success("Eliminado.")
else:
    st.write("—")
