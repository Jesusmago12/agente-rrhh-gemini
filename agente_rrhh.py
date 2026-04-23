import streamlit as st
from google import genai  # Esta es la nueva forma
import pandas as pd
import pdfplumber
import json

# 1. CONFIGURACIÓN CON LA NUEVA LIBRERÍA
try:
    # Inicializamos el cliente con la API Key de tus Secrets
    client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
except Exception as e:
    st.error(f"Error de configuración: {e}")

# ... (Tus funciones de PDF siguen igual) ...

# 2. DENTRO DEL BUCLE (Cuando llamas a la IA), cambia la lógica a esta:
# (Busca donde estaba el 'model.generate_content')
    try:
                response = client.models.generate_content(
                    model='gemini-1.5-flash',
                    contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
                
                # Con la nueva librería, accedemos al texto así:
                res_json = json.loads(response.text)
                
                res_json["archivo"] = archivo.name
                res_json["original"] = texto_cv
                resultados.append(res_json)
    except Exception as e:
                st.error(f"Error técnico en {archivo.name}: {str(e)}")

# --- FUNCIONES DE APOYO ---
def extraer_texto_pdf(archivo):
    texto = ""
    try:
        with pdfplumber.open(archivo) as pdf:
            for pagina in pdf.pages:
                extraido = pagina.extract_text()
                if extraido: texto += extraido + " "
    except Exception as e:
        st.error(f"No se pudo leer el PDF {archivo.name}: {e}")
    return texto.strip()

# --- INTERFAZ ---
st.set_page_config(page_title="IA Recruitment Agent", layout="wide")
st.title("🤖 Agente RRHH Potenciado por Gemini")
st.markdown("### Selección de Perfiles y Equivalencia Profesional")

with st.sidebar:
    st.header("📂 Carga de Currículos")
    archivos_subidos = st.file_uploader("Subir CVs (PDF)", type="pdf", accept_multiple_files=True)
    st.divider()
    st.info("Utilizando arquitectura Cloud para el procesamiento de lenguaje natural.")

job_desc = st.text_area("Describa la vacante y especialidad buscada:", 
                        placeholder="Ej: Ingeniero de Sistemas para mantenimiento de redes...",
                        height=150)

if st.button("🚀 Iniciar Análisis de Perfiles"):
    if not archivos_subidos or not job_desc:
        st.warning("⚠️ Por favor, sube al menos un PDF y escribe la descripción del cargo.")
    else:
        resultados = []
        barra_progreso = st.progress(0)
        
        for idx, archivo in enumerate(archivos_subidos):
            texto_cv = extraer_texto_pdf(archivo)
            
            if texto_cv:
                # PROMPT OPTIMIZADO PARA EVITAR ERRORES DE FORMATO
                prompt = f"""
                Analiza este CV para la vacante: "{job_desc}".
                CV TEXTO: {texto_cv}
                
                REGLAS:
                1. La especialidad (Sistemas, Industrial, etc.) debe coincidir.
                2. Si no tiene el título pero tiene +3 años de experiencia en el área, dar equivalencia.
                
                RESPONDE ÚNICAMENTE EN FORMATO JSON PURO:
                {{
                    "score": (0-100),
                    "años_exp": (número),
                    "validacion": "Título Validado" o "Equivalencia" o "No califica",
                    "razon": "breve explicacion"
                }}
                """
                
                try:
                    response = model.generate_content(prompt,generation_config={"response_mime_type": "application/json"})
                    # Limpieza de respuesta para asegurar JSON válido
                    res_limpia = response.text.replace("```json", "").replace("```", "").strip()
                    res_json = json.loads(res_limpia)
                    
                    res_json["archivo"] = archivo.name
                    res_json["original"] = texto_cv
                    resultados.append(res_json)
                except Exception as e:
                    st.error(f"Error analizando {archivo.name}: El modelo no pudo procesar la solicitud.")
            
            barra_progreso.progress((idx + 1) / len(archivos_subidos))

        # --- MOSTRAR RESULTADOS PROTEGIDOS ---
        if resultados:
            df = pd.DataFrame(resultados)
            
            # Verificamos que existan los datos antes de ordenar
            if 'score' in df.columns:
                df = df.sort_values(by="score", ascending=False)
                
                st.subheader("📊 Ranking de Candidatos")
                st.bar_chart(df.set_index('archivo')['score'])

                for _, row in df.iterrows():
                    with st.expander(f"📄 {row['archivo']} - Compatibilidad: {row['score']}%"):
                        col1, col2 = st.columns([1, 2])
                        col1.metric("Años Exp.", row['años_exp'])
                        col2.info(f"**Estado:** {row['validacion']}")
                        st.write(f"**Justificación de la IA:** {row['razon']}")
                        st.divider()
                        st.caption(f"Extracto: {row['original'][:400]}...")
            else:
                st.error("❌ El formato de respuesta de la IA no fue el esperado. Intenta de nuevo.")
        else:
            st.error("❌ No se obtuvieron resultados. Verifica tu API Key y el estado de los servidores de Google.")