from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

import streamlit as st
from supabase import Client as SupabaseClient
from supabase import create_client


st.set_page_config(page_title="Login RRHH", layout="centered")


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


def contar_busquedas_realizadas(supabase: SupabaseClient) -> tuple[int | None, str | None]:
    try:
        resp = (
            supabase.table("resultados_candidatos")
            .select("nombre_archivo", count="exact", head=True)
            .execute()
        )
        count = getattr(resp, "count", None)
        if count is None:
            data = getattr(resp, "data", None) or []
            count = len(data)
        return int(count), None
    except Exception as exc:
        return None, f"No se pudo consultar resultados_candidatos: {exc}"


def cargar_perfil(supabase: SupabaseClient, user_id: str) -> dict:
    try:
        resp = (
            supabase.table("perfiles")
            .select("id,nombre_completo,email,rol,ultimo_acceso")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return (getattr(resp, "data", None) or {}) if resp else {}
    except Exception:
        return {}


def pintar_estilo() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(180deg, #0b1220 0%, #0f172a 100%);
            color: #e2e8f0;
        }
        .login-card {
            max-width: 440px;
            margin: 6vh auto 0 auto;
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 16px;
            padding: 28px 24px;
            box-shadow: 0 20px 45px rgba(0, 0, 0, 0.35);
        }
        .avatar {
            width: 88px;
            height: 88px;
            margin: 0 auto 18px auto;
            border-radius: 999px;
            display: grid;
            place-items: center;
            background: #1e3a8a;
            border: 2px solid #3b82f6;
            font-size: 44px;
        }
        .title {
            text-align: center;
            margin-bottom: 16px;
            color: #f8fafc;
            font-weight: 700;
            font-size: 1.25rem;
        }
        div[data-testid="stTextInput"] input {
            background: #0f172a;
            border: 1px solid #334155;
            color: #e2e8f0;
            border-radius: 8px;
        }
        div[data-testid="stTextInput"] input:focus {
            border: 1px solid #3b82f6;
            box-shadow: 0 0 0 1px #3b82f6;
        }
        .stButton button {
            width: 100%;
            border: none;
            border-radius: 8px;
            background: linear-gradient(90deg, #ef4444 0%, #f97316 100%);
            color: #fff;
            font-weight: 700;
            padding: 0.6rem 1rem;
        }
        .stButton button:hover {
            filter: brightness(1.08);
        }
        .hint {
            margin-top: 12px;
            font-size: 0.82rem;
            color: #94a3b8;
            text-align: center;
        }
        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #111827 0%, #0f172a 100%);
            border-right: 1px solid #1f2937;
        }
        .metric-card {
            background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%);
            border: 1px solid #334155;
            border-radius: 14px;
            padding: 18px 14px;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 8px;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.25);
            margin-top: 8px;
        }
        .metric-icon {
            width: 34px;
            height: 34px;
            border-radius: 8px;
            background: linear-gradient(180deg, #38bdf8 0%, #0ea5e9 100%);
            display: grid;
            place-items: center;
            font-size: 18px;
        }
        .metric-number {
            color: #f8fafc;
            font-size: 1.85rem;
            line-height: 1;
            font-weight: 800;
        }
        .metric-label {
            color: #93c5fd;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 700;
        }
        .metric-note {
            color: #94a3b8;
            font-size: 0.78rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def guardar_sesion(perfil: dict, user: dict) -> None:
    st.session_state["auth_ok"] = True
    st.session_state["auth_user_id"] = user.get("id")
    st.session_state["auth_email"] = user.get("email")
    st.session_state["auth_rol"] = perfil.get("rol", "usuario")
    st.session_state["auth_nombre"] = perfil.get("nombre_completo") or user.get("email")


def redireccionar_agente() -> None:
    try:
        st.switch_page("pages/agente_rrhh.py")
    except Exception:
        # Fallback sin st.components.v1.html (API deprecada).
        st.markdown(
            '<meta http-equiv="refresh" content="0; url=./pages/agente_rrhh.py">',
            unsafe_allow_html=True,
        )
        st.success("Inicio de sesión correcto. Redirigiendo al asistente...")
        st.stop()


def pintar_sidebar_metricas(total_busquedas: int | None, err_busquedas: str | None) -> None:
    with st.sidebar:
        st.markdown("### Panel RRHH")
        numero = str(total_busquedas) if total_busquedas is not None else "--"
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-icon">👥</div>
                <div class="metric-number">{numero}</div>
                <div class="metric-label">Búsquedas realizadas</div>
                <div class="metric-note">Registros acumulados en AIACTH S.A</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if err_busquedas:
            st.caption(f"No se pudo actualizar el contador: {err_busquedas}")


ocultar_navegacion_streamlit()
pintar_estilo()
supabase, err = obtener_cliente_supabase()
total_busquedas = None
err_busquedas = None
if supabase is not None:
    total_busquedas, err_busquedas = contar_busquedas_realizadas(supabase)
pintar_sidebar_metricas(total_busquedas, err_busquedas)

# Si ya existe sesión válida, redirigir automáticamente.
if st.session_state.get("auth_ok") and st.session_state.get("auth_user_id"):
    redireccionar_agente()

st.markdown("<div class='login-card'>", unsafe_allow_html=True)
st.markdown("<div class='avatar'>👤</div>", unsafe_allow_html=True)
st.markdown("<div class='title'>LOGIN</div>", unsafe_allow_html=True)

correo = st.text_input("Username", placeholder="correo@empresa.com")
clave = st.text_input("Password", type="password", placeholder="********")
col_a, col_b = st.columns(2)
with col_a:
    recordar = st.checkbox("remember me", value=True)
with col_b:
    st.caption("forgot password?")
entrar = st.button("LOGIN", type="primary")

st.markdown(
    "<div class='hint'>Acceso para sistema de reclutamiento y selección</div>",
    unsafe_allow_html=True,
)
st.markdown("</div>", unsafe_allow_html=True)

if err:
    st.error(err)

if entrar:
    if supabase is None:
        st.error("No hay conexión con Supabase.")
    elif not correo or not clave:
        st.warning("Completa usuario y contraseña.")
    else:
        try:
            auth_resp = supabase.auth.sign_in_with_password(
                {"email": correo.strip(), "password": clave}
            )
            user = getattr(auth_resp, "user", None)
            if not user:
                st.error("Credenciales inválidas.")
            else:
                perfil = cargar_perfil(supabase, user.id)
                rol = str(perfil.get("rol", "usuario")).strip().lower()
                if rol not in {"admin", "usuario"}:
                    rol = "usuario"
                perfil["rol"] = rol
                guardar_sesion(perfil, {"id": user.id, "email": user.email})
                if recordar:
                    st.session_state["remember_me"] = True
                redireccionar_agente()
        except Exception as exc:
            st.error(f"No se pudo iniciar sesión: {exc}")
