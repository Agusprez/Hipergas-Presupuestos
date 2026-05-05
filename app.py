import streamlit as st
import pdfplumber
import re
import os
import io
import json
import gspread
from google.oauth2.service_account import Credentials
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from datetime import datetime

# --- FUNCIONES AUXILIARES ---
def num_a_letras(n):
    unidades = ["", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiún", "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
    decenas = ["", "diez", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
    centenas = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos"]

    n = int(n)
    if n == 0: return "cero"
    if n == 100: return "cien"
    if n <= 29: return unidades[n]
    if n < 100:
        d = n // 10
        u = n % 10
        return decenas[d] + (" y " + unidades[u] if u > 0 else "")
    if n < 1000:
        c = n // 100
        resto = n % 100
        return centenas[c] + (" " + num_a_letras(resto) if resto > 0 else "")
    if n < 1000000:
        m = n // 1000
        resto = n % 1000
        str_m = "mil" if m == 1 else num_a_letras(m) + " mil"
        return str_m + (" " + num_a_letras(resto) if resto > 0 else "")
    if n < 1000000000:
        m = n // 1000000
        resto = n % 1000000
        str_m = "un millón" if m == 1 else num_a_letras(m) + " millones"
        return str_m + (" " + num_a_letras(resto) if resto > 0 else "")
    return str(n)

def formato_moneda(valor):
    return f"${valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

# --- LÓGICA DE BASE DE DATOS (GOOGLE SHEETS) ---
def get_gsheets_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    return gspread.authorize(creds)

def init_db():
    try:
        client = get_gsheets_client()
        spreadsheet = client.open_by_url(st.secrets["sheet_url"])
        
        # Verifica si existe la pestaña Historial, si no, la crea automáticamente
        try:
            spreadsheet.worksheet("Historial")
        except gspread.exceptions.WorksheetNotFound:
            sheet_hist = spreadsheet.add_worksheet(title="Historial", rows="1000", cols="5")
            sheet_hist.append_row(["Comprobante", "Fecha", "Cliente", "Total", "Items"])
    except Exception as e:
        st.error(f"Error inicializando Google Sheets: {e}")

def get_siguiente_comprobante():
    try:
        client = get_gsheets_client()
        sheet = client.open_by_url(st.secrets["sheet_url"]).sheet1
        valor = sheet.acell('A2').value
        if not valor:
            return 1
        return int(valor) + 1
    except Exception as e:
        st.error(f"Error de conexión con Google Sheets: {e}")
        return 1

def actualizar_comprobante(numero_usado):
    try:
        client = get_gsheets_client()
        sheet = client.open_by_url(st.secrets["sheet_url"]).sheet1
        sheet.update_acell('A2', numero_usado)
    except Exception as e:
        st.error(f"Error al actualizar la numeración: {e}")

def guardar_en_historial(nro_comprobante, fecha, cliente, total, items_procesados):
    try:
        client = get_gsheets_client()
        sheet_hist = client.open_by_url(st.secrets["sheet_url"]).worksheet("Historial")
        # Convertimos los ítems a texto JSON para guardarlos en una sola celda
        items_str = json.dumps(items_procesados)
        sheet_hist.append_row([nro_comprobante, fecha, cliente, total, items_str])
    except Exception as e:
        st.error(f"Error al guardar en el historial: {e}")

def obtener_historial():
    try:
        client = get_gsheets_client()
        sheet_hist = client.open_by_url(st.secrets["sheet_url"]).worksheet("Historial")
        return sheet_hist.get_all_values()
    except Exception as e:
        return []

# --- LÓGICA DE EXTRACCIÓN ---
def extraer_datos_pdf(archivo_pdf):
    items = []
    cliente = "CLIENTE A DEFINIR"
    text_lines = []
    
    with pdfplumber.open(archivo_pdf) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_lines.extend(text.split('\n'))
    
    patron_remito = re.compile(r'1045\s+(\d+)')
    patron_fecha = re.compile(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})')
    patron_item = re.compile(r'(\d+[.,]\d{2})\s+(.+)')

    current_remito = "S/N"
    current_date = "S/F"

    for i, line in enumerate(text_lines):
        line_clean = line.strip()
        
        if "MUNI" in line_clean or "DIR DE" in line_clean or "TURISMO" in line_clean: 
            texto_sin_numero = re.sub(r'^\d+\s*', '', line_clean)
            texto_sin_direccion = re.sub(r'\s*CARLOS PELLEGRINI.*', '', texto_sin_numero)
            texto_sin_direccion = re.sub(r'\s*SAN PEDRO.*', '', texto_sin_direccion)
            cliente = texto_sin_direccion.strip()

        match_remito = patron_remito.search(line_clean)
        if match_remito:
            current_remito = f"1045-{match_remito.group(1)}"

        fechas_en_linea = patron_fecha.findall(line_clean)
        if fechas_en_linea:
            d, m, y = fechas_en_linea[-1]
            current_date = f"{d.zfill(2)}-{m.zfill(2)}-{y}"
        
        match_item = patron_item.search(line_clean)
        if match_item:
            if "Chofer" not in line and "TOTAL" not in line and "rendición" not in line:
                descripcion = match_item.group(2).strip()
                if len(descripcion) > 3:
                    items.append({
                        'fecha': current_date,
                        'remito': current_remito,
                        'cantidad': float(match_item.group(1).replace(',', '.')),
                        'descripcion': descripcion
                    })
    return cliente, items

# --- GENERADOR DEL PDF ---
def generar_pdf_en_memoria(items_procesados, total_general, cliente_final, fecha_header, nro_comprobante):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    ruta_logo = "logo.png"
    
    if os.path.exists(ruta_logo):
        ancho_logo = 6 * cm
        alto_logo = 2.5 * cm
        x_centro = (width - ancho_logo) / 2
        y_logo = height - 3.5 * cm
        c.drawImage(ruta_logo, x_centro, y_logo, width=ancho_logo, height=alto_logo, preserveAspectRatio=True, mask='auto')
        y_titulo = height - 4.5 * cm
        y_cliente = height - 5.5 * cm
        y_inicio_tabla = height - 7.5 * cm
    else:
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(width/2, height - 2.5 * cm, "Hipergas") 
        y_titulo = height - 3.5 * cm
        y_cliente = height - 4.5 * cm
        y_inicio_tabla = height - 6.5 * cm
        
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 2*cm, height - 2*cm, fecha_header)
    c.drawRightString(width - 2*cm, height - 2.5*cm, f"N° {nro_comprobante}")
    
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width/2, y_titulo, "COTIZACION") 
    
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width/2, y_cliente, cliente_final)
    
    y = y_inicio_tabla
    c.setFont("Helvetica-Bold", 9)
    headers = ["FECHA", "CANTIDAD", "DESCRIPCION", "REMITO", "$ UNITARIO", "TOTAL"]
    x_positions = [2*cm, 4.5*cm, 7*cm, 12.5*cm, 15*cm, 17.5*cm]
    
    for i, h in enumerate(headers): 
        c.drawString(x_positions[i], y, h)
        
    c.line(2*cm, y-0.2*cm, width-2*cm, y-0.2*cm) 
    y -= 0.8*cm
    
    c.setFont("Helvetica", 9)
    for item in items_procesados:
        c.drawString(x_positions[0], y, item['fecha']) 
        c.drawString(x_positions[1], y, f"{int(item['cantidad'])}")
        c.drawString(x_positions[2], y, item['descripcion'][:35])
        c.drawString(x_positions[3], y, item['remito'])
        c.drawString(x_positions[4], y, formato_moneda(item['precio_unitario']))
        c.drawString(x_positions[5], y, formato_moneda(item['total_linea']))
        y -= 0.7*cm
        if y < 5*cm:
            c.showPage()
            y = height - 2*cm
            c.setFont("Helvetica", 9)
    
    y -= 0.5*cm
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(x_positions[4] + 1.5*cm, y, "TOTAL COTIZADO CON IMPUESTOS")
    c.drawRightString(width - 2*cm, y, formato_moneda(total_general))
    
    y -= 1*cm
    texto_pesos = num_a_letras(int(total_general)).capitalize()
    centavos = int(round(total_general % 1, 2) * 100)
    if centavos > 0:
        texto_pesos += f" con {centavos}/100"
    
    c.setFont("Helvetica", 10)
    c.drawString(2*cm, y, f"Total Cotizado: Pesos {texto_pesos}.-")
    
    y_base_texto = 2 * cm
    
    if os.path.exists(ruta_logo):
        ancho_logo_inf = 4 * cm
        alto_logo_inf = 1.5 * cm
        x_centro_logo = (width - ancho_logo_inf) / 2
        y_logo_inf = y_base_texto + 1.5 * cm  
        c.drawImage(ruta_logo, x_centro_logo, y_logo_inf, width=ancho_logo_inf, height=alto_logo_inf, preserveAspectRatio=True, mask='auto')
    else:
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(width/2, y_base_texto + 1.8 * cm, "Hipergas")
        
    c.setFont("Helvetica", 9)
    c.drawCentredString(width/2, y_base_texto + 0.8 * cm, "Alicia Cosentino")
    c.drawCentredString(width/2, y_base_texto + 0.4 * cm, "Comercial")
    c.drawCentredString(width/2, y_base_texto, "Cel. 3329-532867")
    
    c.save()
    buffer.seek(0)
    return buffer


# --- INTERFAZ WEB STREAMLIT ---
st.set_page_config(page_title="Cotizador Hipergas", layout="centered")

# Ejecuta la comprobación/creación de hojas de Excel al inicio
init_db()

st.title("📄 Generador de Presupuestos")

# Creamos dos pestañas en la aplicación
tab1, tab2 = st.tabs(["📝 Crear Presupuesto", "📁 Historial y Descargas"])

# ==========================================
# PESTAÑA 1: CREAR PRESUPUESTO
# ==========================================
with tab1:
    st.header("1. Cargar Remitos")
    uploaded_file = st.file_uploader("Seleccione el archivo PDF con los remitos", type="pdf")

    if uploaded_file is not None:
        cliente_original, items_extraidos = extraer_datos_pdf(uploaded_file)
        
        if not items_extraidos:
            st.error("No se encontraron ítems válidos en el PDF.")
        else:
            st.header("2. Incluir los valores")
            
            with st.form("form_valores"):
                col1, col2, col3 = st.columns(3)
                cliente_final = col1.text_input("Cliente", value=cliente_original)
                fecha_header = col2.text_input("Fecha", value=datetime.now().strftime("%d/%m/%Y").replace('/', '-'))
                
                nro_actual = get_siguiente_comprobante()
                nro_comprobante = col3.text_input("N° Comprobante", value=f"0045-{nro_actual:08d}")
                # --- AGREGAR DESDE ACÁ ---
                st.write("---")
                st.write("**Selección de Ítems (Desmarca los que NO quieras incluir):**")
                
                for i, item in enumerate(items_extraidos):
                    col_chk, col_info = st.columns([1, 9])
                    # Checkbox guardado en session_state con una key única
                    col_chk.checkbox("Incluir", value=True, key=f"item_{i}")
                    col_info.write(f"📅 {item['fecha']} | 📄 Rem: {item['remito']} | 📦 Cant: {item['cantidad']} | 📝 {item['descripcion']}")
                # --- HASTA ACÁ ---
                
                st.write("---")
                st.write("**Precios Unitarios:**")
                descripciones_unicas = sorted(list(set(item['descripcion'] for item in items_extraidos)))
                
                precios_dict = {}
                for desc in descripciones_unicas:
                    col_a, col_b = st.columns([3, 1])
                    col_a.write(f"*{desc}*")
                    precios_dict[desc] = col_b.number_input(f"Precio", key=desc, min_value=0.0, step=1000.0, format="%.2f", label_visibility="collapsed")
                
                st.write("---")
                submitted = st.form_submit_button("Confirmar Valores y Generar PDF", type="primary")

            if submitted:
                # 1. Armamos la lista procesada con los totales, filtrando los no seleccionados
                total_general = 0
                items_procesados = []
                
                # --- REEMPLAZAR EL BUCLE FOR POR ESTE ---
                for i, item in enumerate(items_extraidos):
                    # Solo agregamos el ítem si su checkbox estaba marcado (True)
                    if st.session_state[f"item_{i}"]:
                        p_unit = precios_dict.get(item['descripcion'], 0)
                        
                        # Creamos una copia del diccionario para no arrastrar datos sucios
                        item_final = item.copy()
                        item_final['precio_unitario'] = p_unit
                        item_final['total_linea'] = p_unit * item_final['cantidad']
                        
                        total_general += item_final['total_linea']
                        items_procesados.append(item_final)

                # --- AGREGAR ESTA VALIDACIÓN ---
                # Validamos que haya quedado al menos un ítem después del filtrado
                if len(items_procesados) == 0:
                    st.error("⚠️ No has dejado ningún ítem seleccionado. Marca al menos uno para poder generar el PDF.")
                else:
                    # Todo lo que sigue (Generar PDF, Guardar en Sheets, Botón de descarga)
                    # AHORA TIENE QUE IR INDENTADO (CON UNA TABULACIÓN MÁS) DENTRO DE ESTE ELSE
                    
                    # 2. Generamos el PDF
                    pdf_buffer = generar_pdf_en_memoria(items_procesados, total_general, cliente_final, fecha_header, nro_comprobante)
                    
                    # 3. Guardamos en la base de datos (Google Sheets)
                    with st.spinner('Guardando en la base de datos...'):
                        try:
                            numero_final_usado = int(nro_comprobante.split('-')[1])
                        except:
                            numero_final_usado = nro_actual
                        
                        actualizar_comprobante(numero_final_usado)
                        guardar_en_historial(nro_comprobante, fecha_header, cliente_final, total_general, items_procesados)
                    
                    # 4. Botón de descarga final
                    st.success("¡Presupuesto generado y guardado en el historial!")
                    st.header("3. Descargar Archivo")
                    
                    nombre_limpio = re.sub(r'[\\/*?:"<>|]', "", cliente_final).strip()
                    comp_limpio = re.sub(r'[\\/*?:"<>|]', "", nro_comprobante).strip()
                    nombre_archivo = f"{comp_limpio} - {nombre_limpio}.pdf"
                    
                    st.download_button(
                        label="📥 Descargar PDF Final",
                        data=pdf_buffer,
                        file_name=nombre_archivo,
                        mime="application/pdf"
                    )
# ==========================================
# PESTAÑA 2: HISTORIAL Y DESCARGAS
# ==========================================
with tab2:
    st.header("Historial de Presupuestos Generados")
    
    if st.button("🔄 Actualizar Tabla"):
        pass # Streamlit se recarga automáticamente al tocar el botón
    
    datos_historial = obtener_historial()
    
    if len(datos_historial) > 1: # Chequea que haya datos más allá de los títulos de las columnas
        # Preparamos los datos para mostrarlos lindos en la tabla
        datos_mostrar = []
        for fila in datos_historial[1:]:
            datos_mostrar.append({
                "N° Comprobante": fila[0],
                "Fecha": fila[1],
                "Cliente": fila[2],
                "Total Cotizado": f"${float(fila[3]):,.2f}" if fila[3] else "$0.00"
            })
            
        st.dataframe(datos_mostrar, use_container_width=True)
        
        st.write("---")
        st.subheader("Volver a descargar un presupuesto")
        
        # Creamos la lista de opciones para el selector
        opciones = [f"{fila[0]} - {fila[2]} ({fila[1]})" for fila in datos_historial[1:]]
        seleccion = st.selectbox("Seleccione un comprobante para generar el PDF:", opciones)
        
        if seleccion:
            # Buscamos la fila correspondiente a la selección
            indice = opciones.index(seleccion) + 1
            fila_seleccionada = datos_historial[indice]
            
            comp_hist = fila_seleccionada[0]
            fecha_hist = fila_seleccionada[1]
            cliente_hist = fila_seleccionada[2]
            total_hist = float(fila_seleccionada[3])
            
            # Reconvertimos el texto JSON a la lista de diccionarios original
            items_hist = json.loads(fila_seleccionada[4])
            
            # Generamos el PDF idéntico usando los datos guardados
            pdf_historico = generar_pdf_en_memoria(items_hist, total_hist, cliente_hist, fecha_hist, comp_hist)
            
            nombre_limpio_hist = re.sub(r'[\\/*?:"<>|]', "", cliente_hist).strip()
            st.download_button(
                label=f"📥 Descargar PDF de {comp_hist}",
                data=pdf_historico,
                file_name=f"{comp_hist} - {nombre_limpio_hist}.pdf",
                mime="application/pdf"
            )
    else:
        st.info("Todavía no hay presupuestos guardados en el historial.")
