# Guía de Despliegue: Bot Telegram "NotebookLM-Lite"

Este bot te permite subir documentos PDF (leyes, decretos) y chatear con ellos usando la inteligencia de Google Gemini, similar a NotebookLM.

## Requisitos Previos

Necesitas obtener dos claves (API Keys) antes de empezar:

1.  **Telegram Bot Token**:
    *   Abre Telegram y busca a **@BotFather**.
    *   Envía el comando `/newbot`.
    *   Sigue las instrucciones (ponle nombre y usuario a tu bot).
    *   Copia el **API Token** que te da al final.

2.  **Google Gemini API Key**:
    *   Ve a [Google AI Studio](https://aistudio.google.com/).
    *   Crea una API Key gratuita.

## Despliegue en Render (Gratis)

Render es una plataforma excelente para hostear este bot gratis.

1.  **Sube este código a GitHub**:
    *   Crea un nuevo repositorio en tu cuenta de GitHub (puedes llamarlo `telegram-law-bot`).
    *   Sube los archivos que he creado (`bot.py`, `keep_alive.py`, `Procfile`, `requirements.txt`).

2.  **Crea el servicio en Render**:
    *   Ve a [Render.com](https://render.com/) y crea una cuenta.
    *   Haz clic en "New +" y selecciona **"Web Service"**.
    *   Conecta tu repositorio de GitHub.
    *   Dale un nombre al servicio.
    *   **Runtime**: Python 3.
    *   **Build Command**: `pip install -r requirements.txt`
    *   **Start Command**: `web: gunicorn keep_alive:app & python bot.py` (Debería detectarlo automáticamente del Procfile).
    *   **Plan**: Free.

3.  **Configura las Variables de Entorno (Environment Variables)**:
    *   En la configuración de tu servicio en Render, busca la sección "Environment".
    *   Añade las siguientes variables:
        *   Key: `TELEGRAM_TOKEN` | Value: (Pega tu token de @BotFather)
        *   Key: `GOOGLE_API_KEY` | Value: (Pega tu API Key de Google)

4.  **¡Listo!**:
    *   Dale a "Create Web Service".
    *   Espera unos minutos a que se despliegue.
    *   Una vez diga "Live", ve a tu bot en Telegram, dale `/start` y sube tu primer PDF.

## Uso del Bot

1.  **Inicio**: Envía `/start`.
2.  **Subir**: Arrastra o envía un archivo PDF al chat.
3.  **Preguntar**: Una vez que el bot diga "Analizado", escribe cualquier pregunta. El bot responderá usando solo la información de ese PDF.
4.  **Borrar memoria**: Envía `/clear` para olvidar el documento actual y subir uno nuevo.
