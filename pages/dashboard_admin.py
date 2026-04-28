from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

import streamlit as st
from supabase import Client as SupabaseClient
from supabase import create_client


st.set_page_config(page_title="Dashboard Admin", layout="wide")


def ocultar_navegacion_streamlit() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebarNav"] {display: none;}
        [data-testid="stSidebarNavSeparator"] {display: none;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalizar_supabase_url(url_raw: str) -> str:
    url = (url_raw or "").strip().strip('"').strip("'")
    if not url:
        return ""
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = f"https://{url}"
    parsed = urlparse(url)
    path = (parsed.path or "").strip()
    path = re.sub(r"/+$", "", path)
    path = re.sub(r"(?i)/(rest|auth|storage|functions)/v1$", "", path)
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


@st.cache_resource
def crear_cliente_supabase(url: str, key: str) -> SupabaseClient:
    return create_client(url, key)


def obtener_cliente_supabase() -> tuple[SupabaseClient | None, str | None]:
    try:
        raw_url = st.secrets["SUPABASE_URL"]
        raw_key = st.secrets.get("SUPABASE_ANON_KEY") or st.secrets["SUPABASE_KEY"]
    except Exception:
        return None, (
            "Faltan secretos de Supabase. Configura `SUPABASE_URL` y "
            "`SUPABASE_ANON_KEY` (o `SUPABASE_KEY`) en `.streamlit/secrets.toml`."
        )

    url = normalizar_supabase_url(str(raw_url))
    key = str(raw_key).strip().strip('"').strip("'")
    if not url or not key:
        return None, "Credenciales de Supabase inválidas en secretos."
    try:
        return crear_cliente_supabase(url, key), None
    except Exception as exc:
        return None, f"No se pudo crear el cliente de Supabase: {exc}"


def contar_tabla(supabase: SupabaseClient, tabla: str) -> tuple[int | None, str | None]:
    try:
        resp = supabase.table(tabla).select("*", count="exact", head=True).execute()
        count = getattr(resp, "count", None)
        if count is None:
            data = getattr(resp, "data", None) or []
            count = len(data)
        return int(count), None
    except Exception as exc:
        return None, f"No se pudo consultar `{tabla}`: {exc}"


def pintar_estilo() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(180deg, #0b1220 0%, #0f172a 100%);
            color: #e2e8f0;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.8rem;
        }
        .subtitle {
            color: #94a3b8;
            margin-top: -0.4rem;
            margin-bottom: 1.2rem;
        }
        .card {
            background: linear-gradient(180deg, #162544 0%, #0f1d38 100%);
            border: 1px solid #29426f;
            border-radius: 16px;
            padding: 18px 16px;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.25);
            min-height: 170px;
        }
        .card-icon {
            width: 36px;
            height: 36px;
            border-radius: 9px;
            display: grid;
            place-items: center;
            background: linear-gradient(180deg, #0ea5e9 0%, #0284c7 100%);
            font-size: 18px;
            margin-bottom: 12px;
        }
        .card-number {
            color: #f8fafc;
            font-size: 2.1rem;
            line-height: 1;
            font-weight: 800;
            margin-bottom: 10px;
        }
        .card-label {
            color: #60a5fa;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            font-weight: 700;
            margin-bottom: 8px;
        }
        .card-note {
            color: #93c5fd;
            font-size: 0.9rem;
        }
        .stButton button {
            border: 1px solid #334155;
            border-radius: 8px;
            background: #0f172a;
            color: #e2e8f0;
            font-weight: 600;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def validar_admin() -> None:
    auth_ok = bool(st.session_state.get("auth_ok"))
    user_id = st.session_state.get("auth_user_id")
    rol = str(st.session_state.get("auth_rol", "usuario")).strip().lower()
    if auth_ok and user_id and rol == "admin":
        return

    st.error("Acceso denegado. Este dashboard es exclusivo para usuarios con rol admin.")
    if st.button("Ir a login"):
        try:
            st.switch_page("login.py")
        except Exception:
            st.info("Abre `login.py` para iniciar sesión.")
    st.stop()


def tarjeta_metrica(icono: str, numero: int | None, titulo: str, nota: str) -> None:
    valor = str(numero) if numero is not None else "--"
    st.markdown(
        f"""
        <div class="card">
            <div class="card-icon">{icono}</div>
            <div class="card-number">{valor}</div>
            <div class="card-label">{titulo}</div>
            <div class="card-note">{nota}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def paginacion_sidebar(pagina_actual: str) -> None:
    opciones = {
        "Dashboard admin": "pages/dashboard_admin.py",
        "Asistente RRHH": "pages/agente_rrhh.py",
    }
    etiquetas = list(opciones.keys())
    indice = 0
    for i, etiqueta in enumerate(etiquetas):
        if opciones[etiqueta] == pagina_actual:
            indice = i
            break

    with st.sidebar:
        st.markdown("### Paginación")
        seleccion = st.radio(
            "Cambiar página",
            etiquetas,
            index=indice,
            label_visibility="collapsed",
        )
        destino = opciones[seleccion]
        if destino != pagina_actual:
            st.switch_page(destino)


ocultar_navegacion_streamlit()
pintar_estilo()
validar_admin()
paginacion_sidebar("pages/dashboard_admin.py")

nombre = str(st.session_state.get("auth_nombre", "Administrador")).strip() or "Administrador"
rol = str(st.session_state.get("auth_rol", "admin")).strip().lower()

supabase, err = obtener_cliente_supabase()
total_busquedas = None
total_perfiles = None
err_busquedas = None
err_perfiles = None
if supabase is not None:
    total_busquedas, err_busquedas = contar_tabla(supabase, "resultados_candidatos")
    total_perfiles, err_perfiles = contar_tabla(supabase, "perfiles")

col_title, col_action = st.columns([4, 1])
with col_title:
    st.markdown(
        f"""
        <div class="header">
            <h2>Dashboard Administrativo</h2>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='subtitle'>Sesión activa: <b>{nombre}</b> ({rol})</div>",
        unsafe_allow_html=True,
    )
with col_action:
    if st.button("Cerrar sesión"):
        for key in ["auth_ok", "auth_user_id", "auth_email", "auth_rol", "auth_nombre", "remember_me"]:
            st.session_state.pop(key, None)
        try:
            st.switch_page("login.py")
        except Exception:
            st.rerun()

col_1, col_2 = st.columns(2)
with col_1:
    tarjeta_metrica(
        "👥",
        total_busquedas,
        "Búsquedas realizadas",
        "Registros acumulados en AIACTH S.A",
    )
with col_2:
    tarjeta_metrica(
        "🧾",
        total_perfiles,
        "Perfiles registrados",
        "Total de perfiles cargados en el sistema",
    )

if err:
    st.error(err)
if err_busquedas:
    st.warning(err_busquedas)
if err_perfiles:
    st.warning(err_perfiles)
