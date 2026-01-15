import os
import google.generativeai as genai

def diagnose():
    print("--- Diagnóstico de Modelos de Gemini ---")
    
    # 1. Get Key
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("\n❌ No encontré la variable de entorno GOOGLE_API_KEY en este entorno.")
        print("Por favor, asegúrate de tenerla configurada o pégala manualmente en el script.")
        return

    # 2. Configure
    try:
        genai.configure(api_key=api_key)
    except Exception as e:
        print(f"❌ Error configurando la librería: {e}")
        return

    # 3. List Models
    print(f"\n🔍 Consultando lista de modelos disponibles para tu cuenta...")
    try:
        models = list(genai.list_models())
        print(f"✅ Se encontraron {len(models)} modelos.")
        
        print("\n📋 Modelos que soportan 'generateContent' (Texto/Chat):")
        found_flash = False
        for m in models:
            if 'generateContent' in m.supported_generation_methods:
                print(f"   • {m.name} (Versión: {m.version})")
                if 'flash' in m.name:
                    found_flash = True
        
        if not found_flash:
            print("\n⚠️ ADVERTENCIA: No veo ningún modelo 'Flash' en tu lista.")
            print("   Esto podría explicar el error 404.")
        else:
            print("\n✅ Veo modelos Flash disponibles. Copia EXACTAMENTE uno de los nombres de arriba (ej: models/gemini-1.5-flash) para usarlo en el bot.")

    except Exception as e:
        print(f"\n❌ ERROR CRÍTICO al listar modelos: {e}")
        print("Esto suele significar que la API Key no es válida, o no tiene permisos, o es de un proyecto de Google Cloud (Vertex AI) en lugar de AI Studio.")

if __name__ == "__main__":
    diagnose()
