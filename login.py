from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

import streamlit as st
from supabase import Client as SupabaseClient
from supabase import create_client


st.set_page_config(page_title="Login RRHH", layout="centered")


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


def validar_registro(nombre: str, correo: str, clave: str, confirmar: str) -> str | None:
    if not nombre.strip():
        return "Ingresa tu nombre completo."
    if not correo.strip():
        return "Ingresa un correo válido."
    if not clave:
        return "Ingresa una contraseña."
    if len(clave) < 6:
        return "La contraseña debe tener al menos 6 caracteres."
    if clave != confirmar:
        return "Las contraseñas no coinciden."
    return None


pintar_estilo()
supabase, err = obtener_cliente_supabase()

# Si ya existe sesión válida, redirigir automáticamente.
if st.session_state.get("auth_ok") and st.session_state.get("auth_user_id"):
    redireccionar_agente()

st.markdown("<div class='login-card'>", unsafe_allow_html=True)
st.markdown("<div class='avatar'>👤</div>", unsafe_allow_html=True)
st.markdown("<div class='title'>LOGIN</div>", unsafe_allow_html=True)

modo = st.radio("Acceso", options=["Iniciar sesión", "Registrarse"], horizontal=True)
registrar = False

if modo == "Iniciar sesión":
    correo = st.text_input("Username", placeholder="correo@empresa.com")
    clave = st.text_input("Password", type="password", placeholder="********")
    col_a, col_b = st.columns(2)
    with col_a:
        recordar = st.checkbox("remember me", value=True)
    with col_b:
        st.caption("forgot password?")
    entrar = st.button("LOGIN", type="primary")
else:
    nombre_reg = st.text_input("Nombre completo", placeholder="Ej. María González")
    correo_reg = st.text_input("Correo", placeholder="correo@empresa.com")
    clave_reg = st.text_input("Contraseña", type="password", placeholder="Mínimo 6 caracteres")
    confirmar_reg = st.text_input("Confirmar contraseña", type="password", placeholder="Repite la contraseña")
    entrar = False
    registrar = st.button("REGISTRARME", type="primary")

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

if modo == "Registrarse" and registrar:
    if supabase is None:
        st.error("No hay conexión con Supabase.")
    else:
        error_validacion = validar_registro(nombre_reg, correo_reg, clave_reg, confirmar_reg)
        if error_validacion:
            st.warning(error_validacion)
        else:
            try:
                alta = supabase.auth.sign_up(
                    {
                        "email": correo_reg.strip(),
                        "password": clave_reg,
                        "options": {"data": {"full_name": nombre_reg.strip()}},
                    }
                )
                user = getattr(alta, "user", None)
                if not user:
                    st.error("No se pudo registrar el usuario.")
                else:
                    st.success(
                        "Cuenta creada correctamente. Ya puedes iniciar sesión. "
                        "Si la confirmación por correo está habilitada, revisa tu email."
                    )
            except Exception as exc:
                st.error(f"No se pudo registrar: {exc}")
