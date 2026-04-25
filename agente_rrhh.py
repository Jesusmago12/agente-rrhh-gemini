"""
Asistente de Reclutamiento — RRHH PDVSA Cumaná (Estado Sucre).
Interfaz Streamlit: extracción local de PDF (pdfplumber), evaluación vía Google Gemini.
Los datos solo viven en memoria / st.session_state de la sesión activa (sin persistencia en disco).
"""
from __future__ import annotations

import io
import json
import re
from typing import Any

import pandas as pd
import pdfplumber
import streamlit as st
from google import genai

try:
    from google.genai import errors as genai_errors
except ImportError:  # compatibilidad con versiones antiguas del SDK
    genai_errors = None

# --- Constantes de producto y modelo ---
ORG_NOMBRE = "PDVSA — Recursos Humanos, Gerencia/Área Cumaná, Estado Sucre"
MAX_CV_CHARS = 55_000

# IDs que expone hoy la Gemini API (v1beta). Los antiguos gemini-1.5-flash / -8b suelen dar 404.
# Orden: rápido y amplia compatibilidad → modelo estable más reciente.
# Opcional en secrets.toml / Streamlit Cloud: GEMINI_MODEL_FALLBACK = "modelo1,modelo2"
DEFAULT_MODELOS_GEMINI: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.0-flash",
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
    Devuelve (datos_normalizados, mensaje_error, modelo_usado).
    """
    ids = model_ids if model_ids else modelos_gemini_config()
    fallos: list[str] = []
    for model_id in ids:
        try:
            response = client.models.generate_content(model=model_id, contents=prompt)
            raw = (response.text or "").strip()
            if not raw:
                fallos.append(f"{model_id}: respuesta vacía de la API")
                continue
            data = parsear_json_ia(raw)
            normalizado = normalizar_resultado(data)
            return normalizado, None, model_id
        except json.JSONDecodeError as e:
            fallos.append(f"{model_id}: JSON inválido ({e})")
            continue
        except Exception as e:
            code, msg = _resolver_codigo_error(e)
            etiqueta = "APIError" if _es_api_error(e) else type(e).__name__
            fallos.append(f"{model_id} [{etiqueta} {code or '?'}]: {msg}")
            # Continuar con el siguiente modelo (resiliencia 404/400 u otros)
            continue
    return None, " | ".join(fallos) if fallos else "Error desconocido al contactar modelos.", None


# --- Streamlit ---
st.set_page_config(
    page_title="Asistente RRHH — PDVSA Cumaná",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Asistente de Reclutamiento y Selección")
st.caption(ORG_NOMBRE)
st.markdown(
    "Análisis de currículos **PDF** frente a una vacante, ranking por **mérito técnico** y **experiencia**. "
    "Motor: **Google Gemini** (`google-genai`). Los datos **no se guardan en disco**; solo existen en esta sesión."
)


@st.cache_resource
def crear_cliente_gemini(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def obtener_cliente() -> genai.Client | None:
    try:
        key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        return None
    if not key:
        return None
    return crear_cliente_gemini(str(key))


client = obtener_cliente()
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

with st.sidebar:
    st.header("Configuración")
    archivos_subidos = st.file_uploader(
        "Currículos (PDF)",
        type=["pdf"],
        accept_multiple_files=True,
        help="Los archivos se procesan solo en memoria en esta sesión.",
    )
    st.divider()
    st.markdown("**Modelos (orden de intento)**")
    for m in modelos_gemini_config():
        st.markdown(f"- `{m}`")
    st.caption(
        "Si aparece **404 model not found**, el nombre ya no está disponible en tu clave/API: "
        "actualiza el código o define `GEMINI_MODEL_FALLBACK` en secretos (lista separada por comas). "
        "Lista oficial: [Modelos Gemini](https://ai.google.dev/gemini-api/docs/models)."
    )
    st.divider()
    if client:
        st.success("API Gemini configurada.")
    else:
        st.warning("Sin cliente Gemini hasta configurar secretos.")

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
            st.markdown(f"**Razonamiento técnico:** {row.get('razon', '')}")


if analizar:
    if not archivos_subidos or not (job_desc or "").strip():
        st.warning("Sube al menos un PDF en PDF y describe la vacante.")
    elif client is None:
        st.error("No hay cliente Gemini disponible.")
    else:
        st.session_state.log_errores_rrhh = []
        st.session_state.modelo_info_rrhh = set()
        resultados: list[dict[str, Any]] = []
        modelos_lote = modelos_gemini_config()
        progreso = st.progress(0, text="Iniciando…")
        total = len(archivos_subidos)

        for idx, archivo in enumerate(archivos_subidos):
            nombre = archivo.name
            progreso.progress((idx) / max(total, 1), text=f"Procesando {nombre}…")

            texto_cv, err_pdf = extraer_texto_pdf(archivo)
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
                resultados.append(datos)
                if modelo_usado:
                    st.session_state.modelo_info_rrhh.add(modelo_usado)
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
