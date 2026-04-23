import streamlit as st
import google.generativeai as genai
import pandas as pd
import pdfplumber
import os

# 1. CONFIGURACIÓN DE GEMINI
# Reemplaza con tu API Key o configúrala como secreto en Streamlit Cloud
 
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel('models/gemini-1.5-flash')

# --- FUNCIONES TÉCNICAS ---
def extraer_texto_pdf(archivo):
    texto = ""
    with pdfplumber.open(archivo) as pdf:
        for pagina in pdf.pages:
            extraido = pagina.extract_text()
            if extraido: texto += extraido + " "
    return texto

# --- INTERFAZ ---
st.title("🤖 Agente RRHH Potenciado por Gemini")
st.subheader("Sistema de Selección y Equivalencia Profesional (Cloud)")

with st.sidebar:
    st.header("📂 Carga de CVs")
    archivos_subidos = st.file_uploader("Subir currículos (PDF)", type="pdf", accept_multiple_files=True)
    st.divider()
    st.info("Usando Google Gemini 1.5 para análisis profundo de perfiles.")

job_desc = st.text_area("Describa la vacante y el área de desempeño:", 
                        placeholder="Ej: Ingeniero de Sistemas especializado en Ciberseguridad...",
                        height=150)

if st.button("🚀 Analizar con Inteligencia de Gemini"):
    if not archivos_subidos or not job_desc:
        st.error("Sube archivos y describe el cargo.")
    else:
        resultados = []
        progreso = st.progress(0)
        
        for idx, archivo in enumerate(archivos_subidos):
            texto_cv = extraer_texto_pdf(archivo)
            
            # --- PROMPT DE INGENIERÍA PARA GEMINI ---
            # Aquí le damos tus reglas de negocio y tu fórmula a Gemini
            prompt = f"""
            Actúa como un experto en RRHH técnico para PDVSA. Analiza el siguiente Currículo frente a la Vacante.
            
            VACANTE: {job_desc}
            CURRÍCULO: {texto_cv}
            
            REGLAS DE PUNTUACIÓN (0 a 100):
            1. PRIORIDAD: La especialidad exacta (ej. Sistemas vs Industrial) es CRÍTICA. Si la especialidad no coincide, penaliza el score académico.
            2. EQUIVALENCIA: Si no tiene el título de la profesión pero tiene +3 años en el ÁREA DE DESEMPEÑO, trátalo como un graduado base.
            3. JERARQUÍA: Doctorado > Maestría > Título > Experiencia.
            
            DEVUELVE SOLO UN JSON CON:
            {{
                "score": (número del 0 al 100),
                "años_exp": (número),
                "validacion": "Título Validado" o "Equivalencia por Área" o "No califica",
                "razon": (una breve explicación de por qué este puntaje)
            }}
            """
            
            try:
                response = model.generate_content(prompt)
                # Limpiamos la respuesta para asegurar que sea un JSON válido
                res_text = response.text.replace("```json", "").replace("```", "").strip()
                import json
                res_json = json.loads(res_text)
                
                res_json["archivo"] = archivo.name
                res_json["original"] = texto_cv
                resultados.append(res_json)
            except Exception as e:
                st.error(f"Error analizando {archivo.name}: {e}")
            
            progreso.progress((idx + 1) / len(archivos_subidos))

        # --- MOSTRAR RESULTADOS ---
        df = pd.DataFrame(resultados).sort_values(by="score", ascending=False)
        
        st.bar_chart(df.set_index('archivo')['score'])

        for _, row in df.iterrows():
            with st.expander(f"📄 {row['archivo']} - Score: {row['score']}%"):
                c1, c2 = st.columns([1, 2])
                c1.metric("Años Exp.", row['años_exp'])
                
                if "Validado" in row['validacion']:
                    c2.success(row['validacion'])
                else:
                    c2.warning(row['validacion'])
                
                st.write(f"**Análisis del Agente:** {row['razon']}")
                st.divider()
                st.write("**Extracto del CV:**")
                st.caption(row['original'][:600] + "...")