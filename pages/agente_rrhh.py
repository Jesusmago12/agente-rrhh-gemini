"""
Asistente de Reclutamiento — RRHH PDVSA Cumaná (Estado Sucre).
Interfaz Streamlit: extracción local de PDF (pdfplumber), evaluación vía Google Gemini.
Los datos solo viven en memoria / st.session_state de la sesión activa (sin persistencia en disco).
"""
from __future__ import annotations

import io
import json
import re
from uuid import uuid4
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

import pandas as pd
import pdfplumber
import streamlit as st
from google import genai
from supabase import Client as SupabaseClient
from supabase import create_client

try:
    from google.genai import errors as genai_errors
except ImportError:  # compatibilidad con versiones antiguas del SDK
    genai_errors = None

# --- Constantes de producto y modelo ---
ORG_NOMBRE = "PDVSA — Recursos Humanos, Gerencia/Área Cumaná, Estado Sucre"
MAX_CV_CHARS = 55_000

# IDs que expone hoy la Gemini API (v1beta). Los antiguos gemini-1.5-flash / -8b suelen dar 404.
# Orden: 2.5 Flash (principal) → 3 Flash si 2.5 falla (p. ej. 429 cuota/rate limit) → 2.0 Flash.
# El ID de Gemini 3 puede variar; si da 404, ajusta GEMINI_MODEL_FALLBACK en secretos.
# Opcional: GEMINI_MODEL_FALLBACK = "modelo1,modelo2"
MODELO_GEMINI_25_FLASH = "gemini-2.5-flash"
MODELO_GEMINI_3_FLASH = "gemini-3-flash-preview"
MODELO_GEMINI_20_FLASH = "gemini-2.0-flash"

DEFAULT_MODELOS_GEMINI: tuple[str, ...] = (
    MODELO_GEMINI_25_FLASH,
    MODELO_GEMINI_3_FLASH,
    MODELO_GEMINI_20_FLASH,
)


def modelos_gemini_config() -> tuple[str, ...]:
    """Lista de modelos a probar; se puede anular con el secreto GEMINI_MODEL_FALLBACK (coma-separado)."""
    try:
        override = st.secrets.get("GEMINI_MODEL_FALLBACK")
        if isinstance(override, str) and override.strip():
            t = tuple(m.strip() for m in override.split(",") if m.strip())
            if t:
                return t
    except Exception:
        pass
    return DEFAULT_MODELOS_GEMINI

VALIDACIONES_PERMITIDAS = frozenset({"Apto", "No Apto", "En Observación"})

SYSTEM_INSTRUCTION = f"""Eres un asistente experto en reclutamiento y selección de personal del departamento de Recursos Humanos de {ORG_NOMBRE}.
Tu misión es analizar currículos en texto (extraídos de PDF) y compararlos con la vacante indicada para apoyar un ranking por méritos técnicos y experiencia profesional.
Responde siempre en español, con criterio técnico y objetivo. No inventes experiencia laboral no mencionada en el CV."""


def _resolver_codigo_error(exc: BaseException) -> tuple[int | None, str]:
    """Obtiene código HTTP (si existe) y mensaje legible del SDK."""
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(exc, "status_code", None)
    msg = str(exc)
    if genai_errors and isinstance(exc, genai_errors.APIError):
        msg = getattr(exc, "message", None) or msg
    return (int(code) if code is not None else None), msg


def _es_api_error(exc: BaseException) -> bool:
    if genai_errors and isinstance(exc, genai_errors.APIError):
        return True
    return type(exc).__name__.endswith("APIError")


def truncar_cv(texto: str) -> str:
    if len(texto) <= MAX_CV_CHARS:
        return texto
    return (
        texto[:MAX_CV_CHARS]
        + "\n\n[... contenido truncado por límite seguro de envío a la API ...]"
    )


def extraer_texto_pdf(archivo) -> tuple[str, str | None]:
    """Extrae texto del PDF en memoria. No escribe archivos."""
    nombre = getattr(archivo, "name", "archivo")
    try:
        if hasattr(archivo, "seek"):
            archivo.seek(0)
        raw = archivo.read()
        partes: list[str] = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for pagina in pdf.pages:
                extraido = pagina.extract_text()
                if extraido:
                    partes.append(extraido)
        texto = " ".join(partes).strip()
        return texto, None
    except Exception as e:
        return "", f"No se pudo leer el PDF «{nombre}»: {e}"


def extraer_texto_pdf_desde_bytes(raw: bytes, nombre: str) -> tuple[str, str | None]:
    """Extrae texto de un PDF en bytes para reutilizar lectura en otras operaciones."""
    try:
        partes: list[str] = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for pagina in pdf.pages:
                extraido = pagina.extract_text()
                if extraido:
                    partes.append(extraido)
        texto = " ".join(partes).strip()
        return texto, None
    except Exception as e:
        return "", f"No se pudo leer el PDF «{nombre}»: {e}"


def _limpiar_bloques_markdown(texto: str) -> str:
    t = texto.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def parsear_json_ia(texto: str) -> dict[str, Any]:
    """Parsea JSON devuelto por la IA; tolera fences markdown o texto alrededor."""
    limpio = _limpiar_bloques_markdown(texto)
    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", limpio)
        if not m:
            raise
        return json.loads(m.group())


def normalizar_resultado(data: dict[str, Any]) -> dict[str, Any]:
    """Alinea claves y rangos al contrato de la aplicación."""
    años = data.get("años_exp")
    if años is None:
        años = data.get("anos_exp", 0)
    try:
        años_int = int(años)
    except (TypeError, ValueError):
        años_int = 0

    match = data.get("match_habilidades", data.get("score"))
    try:
        match_int = int(match)
    except (TypeError, ValueError):
        match_int = 0
    match_int = max(0, min(100, match_int))

    val = str(data.get("validacion", "En Observación")).strip()
    if val not in VALIDACIONES_PERMITIDAS:
        val = "En Observación"

    razon = str(data.get("razon", "")).strip() or "Sin razonamiento detallado."

    return {
        "años_exp": años_int,
        "match_habilidades": match_int,
        "validacion": val,
        "razon": razon,
    }


def construir_prompt(vacante: str, texto_cv: str) -> str:
    return f"""{SYSTEM_INSTRUCTION}

VACANTE / REQUISITOS DEL CARGO:
{vacante}

CV DEL CANDIDATO (texto extraído):
{texto_cv}

INSTRUCCIÓN CRÍTICA:
Responde ÚNICAMENTE con un objeto JSON válido (sin markdown, sin texto antes ni después).
Campos obligatorios y significado:
- "años_exp": entero, años totales de experiencia laboral en áreas afines al cargo (estimación conservadora a partir del CV).
- "match_habilidades": entero de 0 a 100, porcentaje de alineación entre requisitos de la vacante y capacidades demostradas en el CV.
- "validacion": exactamente uno de: "Apto", "No Apto", "En Observación".
- "razon": string, explicación técnica breve del match, la experiencia y el veredicto.

Ejemplo de forma (no copies valores):
{{"años_exp": 5, "match_habilidades": 72, "validacion": "En Observación", "razon": "..."}}
"""


def evaluar_cv_con_modelos(
    client: genai.Client,
    prompt: str,
    model_ids: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """
    Intenta modelos en orden. Ante fallo (404 modelo, 400, red, etc.) prueba el siguiente.

    Gemini 3 Flash (`MODELO_GEMINI_3_FLASH`) solo se usa cuando el intento previo a
    `gemini-2.5-flash` falla con error HTTP **429** (cuota / rate limit). En cualquier otro
    fallo de 2.5-flash se omite G3 y se pasa a gemini-2.0-flash.
    """
    ids = model_ids if model_ids else modelos_gemini_config()
    fallos: list[str] = []
    # Tras fallo de 2.5-flash que no sea 429, no gastar cuota en G3 (comportamiento pedido).
    omitir_gemini_3_flash = False

    for model_id in ids:
        if model_id == MODELO_GEMINI_3_FLASH and omitir_gemini_3_flash:
            fallos.append(
                f"{model_id}: omitido (solo se invoca si «{MODELO_GEMINI_25_FLASH}» devuelve **429**)."
            )
            continue

        try:
            response = client.models.generate_content(model=model_id, contents=prompt)
            raw = (response.text or "").strip()
            if not raw:
                fallos.append(f"{model_id}: respuesta vacía de la API")
                if model_id == MODELO_GEMINI_25_FLASH:
                    omitir_gemini_3_flash = True
                continue
            data = parsear_json_ia(raw)
            normalizado = normalizar_resultado(data)
            return normalizado, None, model_id
        except json.JSONDecodeError as e:
            fallos.append(f"{model_id}: JSON inválido ({e})")
            if model_id == MODELO_GEMINI_25_FLASH:
                omitir_gemini_3_flash = True
            else:
                omitir_gemini_3_flash = False
            continue
        except Exception as e:
            code, msg = _resolver_codigo_error(e)
            etiqueta = "APIError" if _es_api_error(e) else type(e).__name__
            fallos.append(f"{model_id} [{etiqueta} {code or '?'}]: {msg}")
            if model_id == MODELO_GEMINI_25_FLASH:
                omitir_gemini_3_flash = not (
                    _es_api_error(e) and code == 429
                )
            else:
                omitir_gemini_3_flash = False
            continue
    return None, " | ".join(fallos) if fallos else "Error desconocido al contactar modelos.", None


def requerir_autenticacion() -> None:
    auth_ok = bool(st.session_state.get("auth_ok"))
    user_id = st.session_state.get("auth_user_id")
    if auth_ok and user_id:
        return

    st.error("Debes iniciar sesión para acceder al asistente de reclutamiento.")
    if st.button("Ir a login"):
        try:
            st.switch_page("login.py")
        except Exception:
            st.info("Abre `login.py` para iniciar sesión.")
    st.stop()


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


def ocultar_sidebar_completo() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] {display: none;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def paginacion_sidebar(pagina_actual: str, es_admin: bool) -> None:
    if es_admin:
        opciones = {
            "Asistente RRHH": "pages/agente_rrhh.py",
            "Dashboard admin": "pages/dashboard_admin.py",
        }
    else:
        opciones = {"Asistente RRHH": "pages/agente_rrhh.py"}

    etiquetas = list(opciones.keys())
    indice = 0
    for i, etiqueta in enumerate(etiquetas):
        if opciones[etiqueta] == pagina_actual:
            indice = i
            break

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


# --- Streamlit ---
st.set_page_config(
    page_title="Asistente RRHH — PDVSA Cumaná",
    layout="wide",
    initial_sidebar_state="expanded",
)
ocultar_navegacion_streamlit()
requerir_autenticacion()

st.title("Asistente de Reclutamiento y Selección")
st.caption(ORG_NOMBRE)
st.markdown(
    "Análisis de currículos **PDF** frente a una vacante, ranking por **mérito técnico** y **experiencia**. "
    "Motor: **Google Gemini** (`google-genai`). Los datos **no se guardan en disco**; solo existen en esta sesión."
)
nombre_usuario = str(st.session_state.get("auth_nombre", "Usuario")).strip() or "Usuario"
rol_usuario = str(st.session_state.get("auth_rol", "usuario")).strip()
col_user, col_logout = st.columns([4, 1])
with col_user:
    st.caption(f"Sesión activa: **{nombre_usuario}** ({rol_usuario})")
with col_logout:
    if st.button("Cerrar sesión"):
        for key in ["auth_ok", "auth_user_id", "auth_email", "auth_rol", "auth_nombre", "remember_me"]:
            st.session_state.pop(key, None)
        try:
            st.switch_page("login.py")
        except Exception:
            st.rerun()


@st.cache_resource
def crear_cliente_gemini(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


@st.cache_resource
def crear_cliente_supabase(url: str, key: str) -> SupabaseClient:
    return create_client(url, key)


def normalizar_supabase_url(url_raw: str) -> str:
    """
    Supabase requiere URL base del proyecto, p. ej.:
    https://<project-ref>.supabase.co
    Si en secretos llega una URL con /rest/v1, /auth/v1, etc., se limpia.
    """
    url = (url_raw or "").strip().strip('"').strip("'")
    if not url:
        return ""
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = f"https://{url}"

    parsed = urlparse(url)
    path = (parsed.path or "").strip()
    path = re.sub(r"/+$", "", path)
    path = re.sub(r"(?i)/(rest|auth|storage|functions)/v1$", "", path)
    normalizada = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return normalizada.rstrip("/")


def obtener_cliente() -> genai.Client | None:
    try:
        key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        return None
    if not key:
        return None
    return crear_cliente_gemini(str(key))


def obtener_cliente_supabase() -> SupabaseClient | None:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        return None
    if not url or not key:
        return None
    url_limpia = normalizar_supabase_url(str(url))
    key_limpia = str(key).strip().strip('"').strip("'")
    if not url_limpia or not key_limpia:
        return None
    try:
        return crear_cliente_supabase(url_limpia, key_limpia)
    except Exception:
        return None


def construir_registro_candidato(
    nombre_archivo: str,
    prompt_busqueda: str,
    datos: dict[str, Any],
    url_pdf: str | None = None,
) -> dict[str, Any]:
    analisis = {
        "años_exp": datos.get("años_exp", 0),
        "match_habilidades": datos.get("match_habilidades", 0),
        "validacion": datos.get("validacion", "En Observación"),
        "razon": datos.get("razon", ""),
    }
    return {
        "nombre_archivo": nombre_archivo,
        "prompt_busqueda": prompt_busqueda,
        "score": int(datos.get("match_habilidades", 0)),
        "experiencia": float(datos.get("años_exp", 0)),
        "validacion": str(datos.get("validacion", "En Observación")),
        "url_pdf": url_pdf or "",
        "analisis_ia": json.dumps(analisis, ensure_ascii=False),
    }


def subir_curriculo_storage_supabase(
    supabase: SupabaseClient, nombre_archivo: str, contenido_pdf: bytes
) -> tuple[str | None, str | None]:
    """
    Sube un PDF al bucket `curriculos` y retorna una URL de acceso.
    Intenta URL firmada y usa URL pública como fallback.
    """
    ruta_storage = f"{uuid4().hex}_{nombre_archivo}"
    try:
        supabase.storage.from_("curriculos").upload(
            ruta_storage,
            contenido_pdf,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        url_pdf: str | None = None
        try:
            signed = supabase.storage.from_("curriculos").create_signed_url(
                ruta_storage, 60 * 60 * 24 * 365
            )
            if isinstance(signed, dict):
                url_pdf = signed.get("signedURL") or signed.get("signed_url")
        except Exception:
            url_pdf = None

        if not url_pdf:
            public_url = supabase.storage.from_("curriculos").get_public_url(
                ruta_storage
            )
            if isinstance(public_url, dict):
                url_pdf = public_url.get("publicURL") or public_url.get("public_url")
            elif isinstance(public_url, str):
                url_pdf = public_url

        if not url_pdf:
            return None, "No se pudo generar URL del archivo en storage."
        return url_pdf, None
    except Exception as e:
        return None, f"Error subiendo PDF a Storage (bucket `curriculos`): {e}"


def guardar_candidato_supabase(
    supabase: SupabaseClient, registro: dict[str, Any]
) -> tuple[bool, str | None]:
    try:
        supabase.table("resultados_candidatos").insert(registro).execute()
        return True, None
    except Exception as e:
        msg = str(e)
        if "PGRST125" in msg or "Invalid path specified in request URL" in msg:
            msg = (
                f"{msg}. Verifica que `SUPABASE_URL` sea la URL base del proyecto "
                "(ej. https://<project-ref>.supabase.co), no una ruta `/rest/v1`."
            )
        return False, msg


def registrar_pdf_para_analisis(
    supabase: SupabaseClient, nombre_archivo: str, url_pdf: str
) -> tuple[bool, str | None]:
    """Registra en BD el PDF cargado para que sea tomado en análisis posteriores."""
    registro = {
        "nombre_archivo": nombre_archivo,
        "prompt_busqueda": "__PDF_CARGADO_STORAGE__",
        "score": 0,
        "experiencia": 0.0,
        "validacion": "En Observación",
        "url_pdf": url_pdf,
        "analisis_ia": json.dumps(
            {
                "estado": "pendiente",
                "detalle": "PDF cargado en bucket. Pendiente de análisis.",
            },
            ensure_ascii=False,
        ),
    }
    return guardar_candidato_supabase(supabase, registro)


def obtener_pdfs_desde_bd(
    supabase: SupabaseClient,
) -> tuple[list[dict[str, str]], str | None]:
    """Consulta en BD las URLs de PDF disponibles para análisis."""
    try:
        resp = (
            supabase.table("resultados_candidatos")
            .select("nombre_archivo,url_pdf")
            .neq("url_pdf", "")
            .execute()
        )
        filas = getattr(resp, "data", None) or []
        vistos: set[str] = set()
        fuentes: list[dict[str, str]] = []
        for fila in filas:
            url_pdf = str((fila or {}).get("url_pdf", "")).strip()
            if not url_pdf or url_pdf in vistos:
                continue
            nombre = str((fila or {}).get("nombre_archivo", "")).strip() or "curriculo.pdf"
            fuentes.append({"nombre_archivo": nombre, "url_pdf": url_pdf})
            vistos.add(url_pdf)
        return fuentes, None
    except Exception as e:
        return [], f"No se pudieron consultar PDFs en base de datos: {e}"


def descargar_pdf_desde_url(url_pdf: str) -> tuple[bytes | None, str | None]:
    """Descarga bytes del PDF usando la URL guardada en base de datos."""
    try:
        req = Request(url_pdf, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=30) as resp:
            contenido = resp.read()
        if not contenido:
            return None, "Descarga vacía."
        return contenido, None
    except Exception as e:
        return None, f"No se pudo descargar el PDF desde URL: {e}"


client = obtener_cliente()
supabase_client = obtener_cliente_supabase()
if client is None:
    st.error(
        "No se encontró `GEMINI_API_KEY` en los secretos de Streamlit. "
        "Configura la clave en *Settings → Secrets* (Streamlit Cloud) o en `.streamlit/secrets.toml` en local."
    )

if "resultados_rrhh" not in st.session_state:
    st.session_state.resultados_rrhh = None
if "log_errores_rrhh" not in st.session_state:
    st.session_state.log_errores_rrhh = []
if "modelo_info_rrhh" not in st.session_state:
    st.session_state.modelo_info_rrhh = None

rol_sidebar = str(st.session_state.get("auth_rol", "usuario")).strip().lower()
archivos_subidos = []
subir_pdfs_storage = False
if rol_sidebar == "admin":
    with st.sidebar:
        st.header("Configuración")
        archivos_subidos = st.file_uploader(
            "Currículos (PDF)",
            type=["pdf"],
            accept_multiple_files=True,
            help="Usa «Subir PDF» para cargarlos al bucket `curriculos` de Supabase Storage.",
        )
        subir_pdfs_storage = st.button(
            "Subir PDF",
            disabled=supabase_client is None,
            help="Sube los PDFs seleccionados al bucket `curriculos` y registra su URL en base de datos.",
        )
        st.divider()
        st.markdown("**Modelos (orden de intento)**")
        for m in modelos_gemini_config():
            st.markdown(f"- `{m}`")
        st.caption(
            "**gemini-3-flash-preview** solo se usa si **gemini-2.5-flash** falla con **429** (cuota). "
            "En otros errores de 2.5 se pasa directo a **gemini-2.0-flash**. "
            "Si ves **404**, cambia el ID en código o en `GEMINI_MODEL_FALLBACK`. "
            "[Modelos Gemini](https://ai.google.dev/gemini-api/docs/models)."
        )
        st.divider()
        if client:
            st.success("API Gemini configurada.")
        else:
            st.warning("Sin cliente Gemini hasta configurar secretos.")
        if supabase_client:
            st.success("Supabase configurado.")
        else:
            st.warning(
                "Sin cliente Supabase (`SUPABASE_URL` y `SUPABASE_KEY`). "
                "Se mostrará el análisis, pero no se guardará en base de datos."
            )
else:
    ocultar_sidebar_completo()

if subir_pdfs_storage:
    if supabase_client is None:
        st.error("No hay cliente Supabase disponible para subir PDFs.")
    elif not archivos_subidos:
        st.warning("Selecciona al menos un PDF en el cargador antes de presionar «Subir PDF».")
    else:
        errores_subida: list[str] = []
        cargados_ok = 0
        for archivo in archivos_subidos:
            nombre = getattr(archivo, "name", "curriculo.pdf")
            try:
                if hasattr(archivo, "seek"):
                    archivo.seek(0)
                raw_pdf = archivo.read()
            except Exception as e:
                errores_subida.append(f"«{nombre}»: no se pudo leer el archivo ({e})")
                continue

            url_pdf_storage, err_storage = subir_curriculo_storage_supabase(
                supabase_client, nombre, raw_pdf
            )
            if err_storage or not url_pdf_storage:
                errores_subida.append(f"«{nombre}»: {err_storage or 'Error de storage'}")
                continue

            ok_reg, err_reg = registrar_pdf_para_analisis(
                supabase_client, nombre, url_pdf_storage
            )
            if not ok_reg:
                errores_subida.append(
                    f"«{nombre}»: PDF subido, pero no se pudo registrar en BD ({err_reg})"
                )
                continue
            cargados_ok += 1

        if cargados_ok:
            st.success(f"PDFs subidos y registrados: **{cargados_ok}**.")
        if errores_subida:
            with st.expander("Incidencias al subir PDF", expanded=True):
                for linea in errores_subida:
                    st.warning(linea)

job_desc = st.text_area(
    "Descripción de la vacante y requisitos mínimos",
    placeholder="Ej.: Ingeniero(a) de petróleo / instrumentación — requisitos, certificaciones, años deseables, ubicación Cumaná/Sucre…",
    height=180,
    help="Cuanto más concreta sea la vacante, mejor el match de habilidades y el razonamiento.",
)

col_a, col_b = st.columns([1, 3])
with col_a:
    analizar = st.button("Analizar candidatos", type="primary", disabled=client is None)
with col_b:
    if st.button("Limpiar resultados de la sesión"):
        st.session_state.resultados_rrhh = None
        st.session_state.log_errores_rrhh = []
        st.session_state.modelo_info_rrhh = None
        st.rerun()


def mostrar_ranking(resultados: list[dict[str, Any]]) -> None:
    df = pd.DataFrame(resultados)
    if "match_habilidades" not in df.columns:
        st.error("Los resultados no incluyen «match_habilidades». Reintenta el análisis.")
        return
    df = df.sort_values(by="match_habilidades", ascending=False)

    st.subheader("Ranking de candidatos (match de habilidades)")
    st.bar_chart(df.set_index("archivo")["match_habilidades"])

    for _, row in df.iterrows():
        pct = row.get("match_habilidades", 0)
        with st.expander(f"{row['archivo']} — Match: {pct}% — {row.get('validacion', '')}"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Match habilidades", f"{pct}%")
            c2.metric("Años experiencia (afín)", f"{row.get('años_exp', 0)}")
            c3.info(f"**Veredicto:** {row.get('validacion', 'N/A')}")
            url_pdf = str(row.get("url_pdf", "") or "").strip()
            if url_pdf:
                st.markdown(f"**Currículo PDF:** [Abrir archivo]({url_pdf})")
            st.markdown(f"**Razonamiento técnico:** {row.get('razon', '')}")


if analizar:
    if not (job_desc or "").strip():
        st.warning("Describe la vacante para poder evaluar los currículos.")
    elif client is None:
        st.error("No hay cliente Gemini disponible.")
    elif supabase_client is None:
        st.error("No hay cliente Supabase para consultar los PDFs guardados.")
    else:
        st.session_state.log_errores_rrhh = []
        st.session_state.modelo_info_rrhh = set()
        resultados: list[dict[str, Any]] = []
        modelos_lote = modelos_gemini_config()
        fuentes_pdf, err_fuentes = obtener_pdfs_desde_bd(supabase_client)
        if err_fuentes:
            st.error(err_fuentes)
            fuentes_pdf = []
        if not fuentes_pdf:
            st.warning(
                "No hay URLs de currículos en base de datos. Usa «Subir PDF» para cargarlos y registrarlos."
            )
            st.stop()
        progreso = st.progress(0, text="Iniciando…")
        total = len(fuentes_pdf)

        for idx, fuente in enumerate(fuentes_pdf):
            nombre = fuente["nombre_archivo"]
            url_pdf = fuente["url_pdf"]
            progreso.progress((idx) / max(total, 1), text=f"Procesando {nombre}…")

            raw_pdf, err_descarga = descargar_pdf_desde_url(url_pdf)
            if err_descarga or raw_pdf is None:
                st.session_state.log_errores_rrhh.append(
                    f"«{nombre}»: {err_descarga or 'No se pudo descargar el PDF.'}"
                )
                progreso.progress((idx + 1) / max(total, 1), text=f"Listo: {nombre}")
                continue

            texto_cv, err_pdf = extraer_texto_pdf_desde_bytes(raw_pdf, nombre)
            if err_pdf:
                st.session_state.log_errores_rrhh.append(err_pdf)
                progreso.progress((idx + 1) / max(total, 1), text=f"Listo: {nombre}")
                continue

            if not texto_cv:
                st.session_state.log_errores_rrhh.append(
                    f"«{nombre}»: no se extrajo texto (¿PDF escaneado sin OCR?)."
                )
                progreso.progress((idx + 1) / max(total, 1), text=f"Listo: {nombre}")
                continue

            prompt = construir_prompt(job_desc.strip(), truncar_cv(texto_cv))
            datos, err_ia, modelo_usado = evaluar_cv_con_modelos(
                client, prompt, modelos_lote
            )

            if datos is not None:
                datos["archivo"] = nombre
                if modelo_usado:
                    st.session_state.modelo_info_rrhh.add(modelo_usado)
                datos["url_pdf"] = url_pdf
                registro = construir_registro_candidato(
                    nombre, job_desc.strip(), datos, url_pdf
                )
                ok_db, err_db = guardar_candidato_supabase(
                    supabase_client, registro
                )
                if not ok_db and err_db:
                    st.session_state.log_errores_rrhh.append(
                        f"«{nombre}»: error al guardar en Supabase ({err_db})"
                    )
                resultados.append(datos)
            else:
                st.session_state.log_errores_rrhh.append(f"«{nombre}»: {err_ia}")

            progreso.progress((idx + 1) / max(total, 1), text=f"Listo: {nombre}")

        progreso.progress(1.0, text="Finalizado")
        st.session_state.resultados_rrhh = resultados

        if st.session_state.log_errores_rrhh:
            with st.expander("Incidencias por archivo (el resto siguió procesándose)", expanded=True):
                for linea in st.session_state.log_errores_rrhh:
                    st.warning(linea)

        if resultados:
            st.success(f"Evaluados con éxito: **{len(resultados)}** de **{total}** currículos.")
            if st.session_state.modelo_info_rrhh:
                st.caption("Modelo(s) usado(s) en al menos un CV: " + ", ".join(sorted(st.session_state.modelo_info_rrhh)))
        else:
            st.warning("No hubo evaluaciones completas. Revisa incidencias y la clave/modelo de Gemini.")

# Mostrar último ranking válido en la sesión (sin escribir a disco)
if st.session_state.resultados_rrhh:
    mostrar_ranking(st.session_state.resultados_rrhh)
