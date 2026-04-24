import streamlit as st
from google import genai
import pandas as pd
import pdfplumber
import json

# --- 1. CONFIGURACIÓN INICIAL ---
st.set_page_config(page_title="IA Recruitment Agent", layout="wide")

try:
    # Usamos la nueva SDK google-genai
    client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
except Exception as e:
    st.error(f"Error al conectar con los secretos de Streamlit: {e}")

# --- 2. FUNCIONES DE PROCESAMIENTO ---
def extraer_texto_pdf(archivo):
    texto = ""
    try:
        with pdfplumber.open(archivo) as pdf:
            for pagina in pdf.pages:
                extraido = pagina.extract_text()
                if extraido:
                    texto += extraido + " "
    except Exception as e:
        st.error(f"Error leyendo PDF {archivo.name}: {e}")
    return texto.strip()

# --- 3. INTERFAZ DE USUARIO ---
st.title("🤖 Agente RRHH - Análisis de Perfiles")
st.markdown("### Clasificación de CVs mediante Inteligencia Artificial")

with st.sidebar:
    st.header("📂 Configuración")
    archivos_subidos = st.file_uploader("Subir Currículos (PDF)", type="pdf", accept_multiple_files=True)
    st.divider()
    st.write("Estado: **Conectado a Gemini Pro**")

job_desc = st.text_area("Describa el perfil buscado y requisitos mínimos:", 
                        placeholder="Ej: Ingeniero de Sistemas con experiencia en Python...",
                        height=150)

if st.button("🚀 Analizar Candidatos"):
    if not archivos_subidos or not job_desc:
        st.warning("⚠️ Sube los CVs y escribe la descripción del cargo para continuar.")
    else:
        resultados = []
        progreso = st.progress(0)
        
        for idx, archivo in enumerate(archivos_subidos):
            texto_cv = extraer_texto_pdf(archivo)
            
            if texto_cv:
                # Prompt estructurado para respuesta JSON
                prompt = f"""
                Actúa como un experto en reclutamiento técnico.
                Analiza el siguiente CV basándote en esta vacante: "{job_desc}"
                
                CV DEL CANDIDATO:
                {texto_cv}
                
                Instrucciones:
                1. Evalúa de 0 a 100 la compatibilidad.
                2. Identifica años de experiencia total.
                3. Determina si califica por título o equivalencia profesional.
                
                Responde estrictamente en este formato JSON:
                {{
                    "score": int,
                    "años_exp": int,
                    "validacion": "string",
                    "razon": "string"
                }}
                """
                
                try:
                    # Llamada a la nueva SDK
                    response = client.models.generate_content(
                        model='gemini-pro',
                        contents=prompt,
                        config={
                            'response_mime_type': 'application/json',
                        }
                    )
                    
                    # Parsear la respuesta JSON
                    res_data = json.loads(response.text)
                    res_data["archivo"] = archivo.name
                    res_data["resumen_cv"] = texto_cv[:500] # Para mostrar un extracto
                    resultados.append(res_data)
                    
                except Exception as e:
                    st.error(f"Error técnico con {archivo.name}: {str(e)}")
            
            progreso.progress((idx + 1) / len(archivos_subidos))

        # --- 4. VISUALIZACIÓN DE RESULTADOS PROTEGIDA ---
        if resultados:
            df = pd.DataFrame(resultados)
            
            # Verificamos que la IA realmente devolvió la columna 'score'
            if 'score' in df.columns:
                df = df.sort_values(by="score", ascending=False)
                
                st.subheader("📊 Ranking de Candidatos Seleccionados")
                st.bar_chart(df.set_index('archivo')['score'])

                for _, row in df.iterrows():
                    with st.expander(f"📄 {row['archivo']} - Match: {row.get('score', 0)}%"):
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Puntuación", f"{row.get('score', 0)}%")
                        c2.metric("Experiencia", f"{row.get('años_exp', 0)} años")
                        c3.info(f"**Veredicto:** {row.get('validacion', 'N/A')}")
                        
                        st.write(f"**Análisis de la IA:** {row.get('razon', 'No se pudo generar análisis.')}")
            else:
                st.error("❌ La IA no devolvió el formato esperado. Intenta simplificar la descripción de la vacante.")
        else:
            st.warning("⚠️ No se pudieron obtener análisis. Revisa los mensajes de error arriba.")
