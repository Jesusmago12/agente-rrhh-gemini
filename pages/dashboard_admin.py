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


def obtener_cliente_supabase_admin() -> SupabaseClient | None:
    try:
        raw_url = st.secrets["SUPABASE_URL"]
        raw_key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
    except Exception:
        return None
    if not raw_key:
        return None
    url = normalizar_supabase_url(str(raw_url))
    key = str(raw_key).strip().strip('"').strip("'")
    if not url or not key:
        return None
    try:
        return crear_cliente_supabase(url, key)
    except Exception:
        return None


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


def contar_archivos_storage(
    supabase: SupabaseClient, bucket: str = "curriculos"
) -> tuple[int | None, str | None]:
    try:
        objetos = supabase.storage.from_(bucket).list("", {"limit": 1000, "offset": 0})
        if not isinstance(objetos, list):
            return 0, None

        total = 0
        for obj in objetos:
            nombre = str((obj or {}).get("name", "")).strip()
            # Ignora carpetas virtuales y entradas sin nombre.
            if nombre and "." in nombre:
                total += 1
        return total, None
    except Exception as exc:
        return None, f"No se pudo contar archivos del bucket `{bucket}`: {exc}"


def listar_usuarios(supabase: SupabaseClient) -> tuple[list[dict[str, str]], str | None]:
    try:
        resp = (
            supabase.table("perfiles")
            .select("nombre_completo,email")
            .order("nombre_completo", desc=False)
            .execute()
        )
        filas = getattr(resp, "data", None) or []
        usuarios: list[dict[str, str]] = []
        for fila in filas:
            nombre = str((fila or {}).get("nombre_completo", "")).strip()
            email = str((fila or {}).get("email", "")).strip()
            if nombre or email:
                usuarios.append({"nombre_completo": nombre, "email": email})
        return usuarios, None
    except Exception as exc:
        return [], f"No se pudo consultar la tabla `perfiles`: {exc}"


def crear_usuario_perfil(
    supabase: SupabaseClient,
    supabase_admin: SupabaseClient | None,
    nombre_completo: str,
    email: str,
    rol: str,
    password: str,
) -> tuple[bool, str | None]:
    email_normalizado = email.strip().lower()
    nombre_limpio = nombre_completo.strip()
    rol_limpio = rol.strip().lower()
    user_id: str | None = None

    metadata = {"nombre_completo": nombre_limpio,"full_name": nombre_limpio, "rol": rol_limpio}

    try:
        # Si existe service role key, usar create_user (flujo admin).
        if supabase_admin is not None:
            auth_resp = supabase_admin.auth.admin.create_user(
                {
                    "email": email_normalizado,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": metadata,
                }
            )
        else:
            # Fallback con sign_up estándar.
            auth_resp = supabase.auth.sign_up(
                {
                    "email": email_normalizado,
                    "password": password,
                    "options": {"data": metadata},
                }
            )

        user = getattr(auth_resp, "user", None)
        if user:
            user_id = getattr(user, "id", None)
        if not user_id and isinstance(auth_resp, dict):
            user_obj = auth_resp.get("user") or {}
            user_id = user_obj.get("id")
    except Exception as exc:
        return False, f"No se pudo crear el usuario en autenticación: {exc}"

    try:
        registro = {
            "nombre_completo": nombre_limpio,
            "email": email_normalizado,
            "rol": rol_limpio,
        }
        if user_id:
            registro["id"] = user_id

        # Upsert evita duplicados si ya existe perfil (por trigger o registro previo).
        supabase.table("perfiles").upsert(registro).execute()
        return True, None
    except Exception as exc:
        return False, f"No se pudo sincronizar el perfil en `perfiles`: {exc}"


def eliminar_usuario_perfil(
    supabase: SupabaseClient, nombre_completo: str, email: str
) -> tuple[bool, str | None]:
    try:
        resp = (
            supabase.table("perfiles")
            .delete()
            .eq("nombre_completo", nombre_completo.strip())
            .eq("email", email.strip().lower())
            .execute()
        )
        filas = getattr(resp, "data", None) or []
        if len(filas) == 0:
            return False, "No se encontró un usuario con ese nombre y correo."
        return True, None
    except Exception as exc:
        return False, f"No se pudo eliminar el usuario de `perfiles`: {exc}"


def refrescar_lista_si_visible(supabase: SupabaseClient | None) -> None:
    if not st.session_state.get("mostrar_usuarios_admin"):
        return
    if supabase is None:
        st.session_state["usuarios_admin_lista"] = []
        return
    usuarios, err_msg = listar_usuarios(supabase)
    if err_msg:
        st.session_state["admin_feedback"] = ("error", err_msg)
        return
    st.session_state["usuarios_admin_lista"] = usuarios


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


def tarjeta_metrica(
    icono: str,
    numero: int | None,
    titulo: str,
    nota: str,
    detalle_extra: str | None = None,
) -> None:
    valor = str(numero) if numero is not None else "--"
    detalle_html = f"<div class='card-note'>{detalle_extra}</div>" if detalle_extra else ""
    st.markdown(
        f"""
        <div class="card">
            <div class="card-icon">{icono}</div>
            <div class="card-number">{valor}</div>
            <div class="card-label">{titulo}</div>
            <div class="card-note">{nota}</div>
            {detalle_html}
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


@st.dialog("Crear usuario")
def modal_crear_usuario(supabase: SupabaseClient | None) -> None:
    st.write("Completa los datos del usuario que se registrará.")
    with st.form("form_crear_usuario"):
        nombre = st.text_input("Nombre completo")
        email = st.text_input("Email")
        password = st.text_input("Contraseña", type="password")
        password_confirm = st.text_input("Confirmar contraseña", type="password")
        rol = st.selectbox("Rol", ["usuario", "admin"], index=0)
        confirmar = st.form_submit_button("Crear usuario")

    if confirmar:
        if supabase is None:
            st.error("No hay conexión con Supabase.")
            return
        if not nombre.strip() or not email.strip() or not password or not password_confirm:
            st.warning("Completa nombre, email, contraseña y confirmación.")
            return
        if len(password) < 6:
            st.warning("La contraseña debe tener al menos 6 caracteres.")
            return
        if password != password_confirm:
            st.warning("La confirmación de contraseña no coincide.")
            return
        supabase_admin = obtener_cliente_supabase_admin()
        ok, err_msg = crear_usuario_perfil(
            supabase,
            supabase_admin,
            nombre,
            email,
            rol,
            password,
        )
        if ok:
            refrescar_lista_si_visible(supabase)
            st.session_state["admin_feedback"] = (
                "success",
                f"Usuario creado correctamente: {nombre.strip()} ({email.strip().lower()}).",
            )
            st.rerun()
        st.error(err_msg or "No se pudo crear el usuario.")


@st.dialog("Eliminar usuario")
def modal_eliminar_usuario() -> None:
    st.write("Indica los datos del usuario que deseas eliminar.")
    with st.form("form_eliminar_usuario"):
        nombre = st.text_input("Nombre completo")
        email = st.text_input("Email")
        continuar = st.form_submit_button("Eliminar usuario")

    if continuar:
        if not nombre.strip() or not email.strip():
            st.warning("Debes completar nombre y email.")
            return
        st.session_state["pending_delete_usuario"] = {
            "nombre_completo": nombre.strip(),
            "email": email.strip().lower(),
        }
        st.rerun()


@st.dialog("Confirmar eliminación")
def modal_confirmar_eliminacion(supabase: SupabaseClient | None) -> None:
    pendiente = st.session_state.get("pending_delete_usuario")
    if not pendiente:
        st.info("No hay usuario pendiente por eliminar.")
        return
    nombre = pendiente.get("nombre_completo", "")
    email = pendiente.get("email", "")
    st.warning("Estas seguro que quieres eliminar este usuario?")
    st.caption(f"Usuario: {nombre} ({email})")
    col_ok, col_no = st.columns(2)
    with col_ok:
        confirmar = st.button("Si, eliminar empleado", type="primary")
    with col_no:
        cancelar = st.button("Cancelar")

    if cancelar:
        st.session_state["pending_delete_usuario"] = None
        st.session_state["admin_feedback"] = ("info", "Eliminación cancelada.")
        st.rerun()

    if confirmar:
        if supabase is None:
            st.error("No hay conexión con Supabase.")
            return
        ok, err_msg = eliminar_usuario_perfil(supabase, nombre, email)
        st.session_state["pending_delete_usuario"] = None
        if ok:
            refrescar_lista_si_visible(supabase)
            st.session_state["admin_feedback"] = (
                "success",
                f"Usuario eliminado correctamente: {nombre} ({email}).",
            )
        else:
            st.session_state["admin_feedback"] = (
                "error",
                err_msg or "No se pudo eliminar el usuario.",
            )
        st.rerun()


def configuracion_usuarios_sidebar(supabase: SupabaseClient | None) -> None:
    with st.sidebar:
        st.divider()
        st.markdown("### Configuracion de usuarios")
        mostrar = st.button("Mostrar todos los usuarios", use_container_width=True)
        crear = st.button("Crear usuario", use_container_width=True)
        eliminar = st.button("Eliminar usuario", use_container_width=True)

    if mostrar:
        if supabase is None:
            st.session_state["admin_feedback"] = ("error", "No hay conexión con Supabase.")
            st.session_state["mostrar_usuarios_admin"] = False
        else:
            usuarios, err_msg = listar_usuarios(supabase)
            if err_msg:
                st.session_state["admin_feedback"] = ("error", err_msg)
                st.session_state["mostrar_usuarios_admin"] = False
            else:
                st.session_state["usuarios_admin_lista"] = usuarios
                st.session_state["mostrar_usuarios_admin"] = True

    if crear:
        modal_crear_usuario(supabase)

    if eliminar:
        modal_eliminar_usuario()

    if st.session_state.get("pending_delete_usuario"):
        modal_confirmar_eliminacion(supabase)


ocultar_navegacion_streamlit()
pintar_estilo()
validar_admin()
paginacion_sidebar("pages/dashboard_admin.py")

nombre = str(st.session_state.get("auth_nombre", "Administrador")).strip() or "Administrador"
rol = str(st.session_state.get("auth_rol", "admin")).strip().lower()

supabase, err = obtener_cliente_supabase()
if "mostrar_usuarios_admin" not in st.session_state:
    st.session_state["mostrar_usuarios_admin"] = False
if "usuarios_admin_lista" not in st.session_state:
    st.session_state["usuarios_admin_lista"] = []
if "pending_delete_usuario" not in st.session_state:
    st.session_state["pending_delete_usuario"] = None
if "admin_feedback" not in st.session_state:
    st.session_state["admin_feedback"] = None

configuracion_usuarios_sidebar(supabase)

total_busquedas = None
total_perfiles = None
total_curriculos_storage = None
err_busquedas = None
err_perfiles = None
err_storage = None
if supabase is not None:
    total_busquedas, err_busquedas = contar_tabla(supabase, "resultados_candidatos")
    total_perfiles, err_perfiles = contar_tabla(supabase, "perfiles")
    total_curriculos_storage, err_storage = contar_archivos_storage(supabase, "curriculos")

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
    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    total_curriculos = total_curriculos_storage-1
    tarjeta_metrica( 
        "📄",
        total_curriculos,
        "Currículos cargados",
        "Total de archivos en Storage (bucket curriculos)",
    )
with col_2:
    tarjeta_metrica(
        "🧾",
        total_perfiles,
        "usuarios registrados",
        "Total de perfiles cargados en el sistema",
    )

feedback = st.session_state.get("admin_feedback")
if feedback:
    nivel, mensaje = feedback
    if nivel == "success":
        st.success(mensaje)
    elif nivel == "error":
        st.error(mensaje)
    else:
        st.info(mensaje)
    st.session_state["admin_feedback"] = None

if st.session_state.get("mostrar_usuarios_admin"):
    st.markdown("### Usuarios registrados")
    usuarios = st.session_state.get("usuarios_admin_lista", [])
    if not usuarios:
        st.info("No hay usuarios para mostrar.")
    else:
        st.dataframe(
            usuarios,
            use_container_width=True,
            hide_index=True,
            column_config={
                "nombre_completo": "Nombre",
                "email": "Email",
            },
        )

if err:
    st.error(err)
if err_busquedas:
    st.warning(err_busquedas)
if err_perfiles:
    st.warning(err_perfiles)
if err_storage:
    st.warning(err_storage)
